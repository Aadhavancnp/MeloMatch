import random
import logging
from collections import Counter, defaultdict

from django.db.models import Count, Q
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

from music.models import Track, UserLikedTrack # Assuming UserLikedTrack model exists
from users.models import CustomUser # For type hinting if needed
from services.recommendation_service.utils import (
    _get_standardized_features_for_track,
    STANDARD_SIMILARITY_FEATURES
)
from .models import UserRecommendationCache # Import the cache model
from django.utils import timezone
from datetime import timedelta

# To avoid circular dependency if spotify_service.client needs recommendation functions,
# we might need to pass sp_client instance or necessary functions directly.
# For now, assuming _get_standardized_features_for_track can get sp_client if needed (e.g. via user object or passed explicitly)
# The _get_standardized_features_for_track in utils.py was designed to accept sp_client.

logger = logging.getLogger(__name__)

def get_content_based_recommendations(user: CustomUser, seed_track_obj: Track, num_recommendations=10, all_tracks_qs=None, sp_client=None):
    """
    Generates content-based recommendations similar to a seed track.
    `sp_client` is needed by _get_standardized_features_for_track if features need to be fetched from Spotify.
    """
    logger.info(f"Generating content-based recommendations for user {user.username} based on seed track: {seed_track_obj.title}")

    seed_features_vector = _get_standardized_features_for_track(seed_track_obj, sp_client=sp_client)
    if not seed_features_vector:
        logger.warning(f"Could not get features for seed track {seed_track_obj.title}. Aborting content-based.")
        return []

    if all_tracks_qs is None:
        # Fallback: Get a sample of tracks, excluding the seed track and tracks already liked by the user.
        # This queryset should ideally be broader and pre-filtered for relevance (e.g., by genre, recent additions).
        # For a simple PoC, we might take recent tracks or a random sample.

        liked_track_ids = UserLikedTrack.objects.filter(user=user).values_list('track_id', flat=True)

        candidate_tracks_qs = Track.objects.exclude(spotify_id=seed_track_obj.spotify_id)\
                                           .exclude(id__in=liked_track_ids)\
                                           .prefetch_related('artists', 'genres')[:200] # Limit candidates for performance
        logger.info(f"Fetched {candidate_tracks_qs.count()} candidate tracks for content-based filtering.")
    else:
        # Ensure we exclude the seed track if it's part of the provided queryset
        candidate_tracks_qs = all_tracks_qs.exclude(spotify_id=seed_track_obj.spotify_id)


    similarities = []
    for candidate_track in candidate_tracks_qs:
        # Redundant check if all_tracks_qs already excluded it, but safe.
        if candidate_track.spotify_id == seed_track_obj.spotify_id:
            continue

        candidate_features_vector = _get_standardized_features_for_track(candidate_track, sp_client=sp_client)
        if not candidate_features_vector:
            logger.debug(f"Skipping candidate track {candidate_track.title} due to missing features.")
            continue

        seed_np = np.array(seed_features_vector).reshape(1, -1)
        candidate_np = np.array(candidate_features_vector).reshape(1, -1)

        sim_score = cosine_similarity(seed_np, candidate_np)[0][0]
        similarities.append((candidate_track, sim_score))

    similarities.sort(key=lambda x: x[1], reverse=True)

    recommended_tracks = [track for track, score in similarities[:num_recommendations]]
    logger.info(f"Content-based recommendations for {seed_track_obj.title}: {[t.title for t in recommended_tracks]}")
    return recommended_tracks


def get_item_item_collaborative_recommendations(user: CustomUser, seed_track_obj: Track, num_recommendations=10):
    """
    Generates item-item collaborative filtering recommendations.
    Finds users who liked the seed_track_obj, then recommends other tracks liked by those users.
    """
    logger.info(f"Generating item-item CF recommendations for user {user.username} based on seed track: {seed_track_obj.title}")

    # Find other users who liked the seed track
    # Exclude the current user, and ensure users are distinct
    similar_users = CustomUser.objects.filter(
        liked_spotify_tracks__track=seed_track_obj
    ).exclude(id=user.id).distinct()

    if not similar_users.exists():
        logger.info(f"No other users found who liked {seed_track_obj.title}. Cannot generate item-item CF recs.")
        return []

    logger.info(f"Found {similar_users.count()} users who also liked {seed_track_obj.title}.")

    # Get all tracks liked by these similar users, excluding the seed track itself
    # And excluding tracks already liked by the target user
    user_liked_track_ids = UserLikedTrack.objects.filter(user=user).values_list('track_id', flat=True)

    co_liked_tracks = UserLikedTrack.objects.filter(
        user__in=similar_users
    ).exclude(
        Q(track=seed_track_obj) | Q(track_id__in=user_liked_track_ids)
    ).values(
        'track__spotify_id', 'track__title', 'track__id' # Include track_id for fetching Track object
    ).annotate(
        times_liked=Count('track__spotify_id')
    ).order_by('-times_liked')
    # The .values().annotate().order_by() groups by the fields in values() and counts occurrences.

    if not co_liked_tracks:
        logger.info(f"No co-liked tracks found for seed {seed_track_obj.title} from similar users.")
        return []

    # Get the actual Track objects for the recommended tracks
    # We need to fetch the full Track objects for consistency, prefetching artists for display
    recommended_track_ids = [item['track__id'] for item in co_liked_tracks[:num_recommendations]]

    # Fetch in original recommendation order if possible, though order_by('-times_liked') already sorts by preference
    # This might re-order if PKs are not in 'times_liked' order.
    # A more robust way if specific order from annotate is critical:
    # recommended_tracks = [Track.objects.get(id=tid) for tid in recommended_track_ids] # N+1
    # Better:
    recommended_tracks_qs = Track.objects.filter(id__in=recommended_track_ids).prefetch_related('artists', 'genres')
    # To maintain the order from `co_liked_tracks`:
    tracks_dict = {track.id: track for track in recommended_tracks_qs}
    final_recommendations = [tracks_dict[tid] for tid in recommended_track_ids if tid in tracks_dict]

    logger.info(f"Item-item CF recommendations for {seed_track_obj.title}: {[t.title for t in final_recommendations]}")
    return final_recommendations


def get_hybrid_recommendations(user: CustomUser, seed_track_id: str, num_recommendations=10, context: str = "default_hybrid"):
    """
    Generates hybrid recommendations by combining content-based and collaborative filtering results
    using an interleaving strategy. Uses UserRecommendationCache.
    """
    logger.info(f"Attempting to fetch hybrid recommendations for user {user.username}, seed_track_id: {seed_track_id}, context: {context}")

    # 1. Cache Check
    try:
        cached_recs_obj = UserRecommendationCache.objects.get(user=user, context=context)
        if not cached_recs_obj.is_expired():
            logger.info(f"Found valid cache for user {user.username}, context {context}. Fetching tracks.")
            # recommended_track_ids = cached_recs_obj.recommended_track_ids
            # tracks_qs = Track.objects.filter(spotify_id__in=recommended_track_ids).prefetch_related('artists', 'genres')
            # tracks_map = {track.spotify_id: track for track in tracks_qs}
            # ordered_tracks = [tracks_map[spotify_id] for spotify_id in recommended_track_ids if spotify_id in tracks_map]
            ordered_tracks = cached_recs_obj.get_tracks() # Use the model's helper method
            if ordered_tracks: # Ensure tracks were found from IDs
                 logger.info(f"Returning {len(ordered_tracks)} tracks from cache for user {user.username}, context {context}.")
                 return ordered_tracks
            else:
                logger.warning(f"Cached track IDs for user {user.username}, context {context} resulted in no tracks. Re-generating.")
        else:
            logger.info(f"Cache expired for user {user.username}, context {context}. Re-generating.")
    except UserRecommendationCache.DoesNotExist:
        logger.info(f"No cache found for user {user.username}, context {context}. Generating new recommendations.")
    except Exception as e:
        logger.error(f"Error accessing recommendation cache for user {user.username}, context {context}: {e}")
        # Proceed to generate new recommendations if cache access fails badly

    # 2. If Cache Miss or Stale, Generate New Recommendations
    logger.info(f"Generating new hybrid recommendations for user {user.username}, seed_track_id: {seed_track_id}, context: {context}")
    try:
        seed_track_obj = Track.objects.select_related('genres').prefetch_related('artists').get(spotify_id=seed_track_id)
    except Track.DoesNotExist:
        logger.error(f"Seed track with ID {seed_track_id} not found for hybrid recommendations.")
        return []

    # Conceptual: Obtain sp_client if _get_standardized_features_for_track needs it for fresh feature fetching.
    # This client would ideally be passed down from the view, which gets it based on the request.user.
    # For background tasks, get_spotify_client_for_user(user.id) would be used.
    # For simplicity in this PoC, we'll pass sp_client=None, assuming features are mostly cached or can fallback.
    sp_client = None
    # Example if client was needed:
    # from services.spotify_service.client import get_spotify_client_for_user
    # if hasattr(user, 'spotify_refresh_token') and user.spotify_refresh_token: # Check if user has tokens
    #     sp_client = get_spotify_client_for_user(user.id)

    fetch_count = num_recommendations + 5
    content_recs = get_content_based_recommendations(user, seed_track_obj, num_recommendations=fetch_count, sp_client=sp_client)
    collab_recs = get_item_item_collaborative_recommendations(user, seed_track_obj, num_recommendations=fetch_count)

    newly_generated_tracks = []
    seen_track_ids = {seed_track_obj.spotify_id}
    user_liked_track_spotify_ids = UserLikedTrack.objects.filter(user=user).values_list('track__spotify_id', flat=True)
    seen_track_ids.update(user_liked_track_spotify_ids)

    ptr_content, ptr_collab = 0, 0
    while len(newly_generated_tracks) < num_recommendations:
        added_flag = False
        if ptr_content < len(content_recs):
            track = content_recs[ptr_content]
            if track.spotify_id not in seen_track_ids:
                newly_generated_tracks.append(track); seen_track_ids.add(track.spotify_id); added_flag = True
            ptr_content += 1
            if len(newly_generated_tracks) >= num_recommendations: break

        if ptr_collab < len(collab_recs):
            track = collab_recs[ptr_collab]
            if track.spotify_id not in seen_track_ids:
                newly_generated_tracks.append(track); seen_track_ids.add(track.spotify_id); added_flag = True
            ptr_collab += 1
            if len(newly_generated_tracks) >= num_recommendations: break

        if not added_flag and (ptr_content >= len(content_recs) and ptr_collab >= len(collab_recs)):
            break

    # 3. Store Results in Cache
    if newly_generated_tracks:
        track_ids_to_cache = [track.spotify_id for track in newly_generated_tracks]
        expiry_time = timezone.now() + timedelta(days=1) # Cache for 1 day

        UserRecommendationCache.objects.update_or_create(
            user=user, context=context,
            defaults={
                'recommended_track_ids': track_ids_to_cache,
                'generated_at': timezone.now(), # update_or_create will update this
                'expires_at': expiry_time
            }
        )
        logger.info(f"Saved/Updated {len(track_ids_to_cache)} recommendations to cache for user {user.username}, context {context}.")

    logger.info(f"Hybrid recommendations generated for {seed_track_obj.title}: {[t.title for t in newly_generated_tracks]}")
    return newly_generated_tracks
