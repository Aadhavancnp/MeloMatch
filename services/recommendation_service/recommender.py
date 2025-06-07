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


def get_hybrid_recommendations(user: CustomUser, seed_track_id: str, num_recommendations=10):
    """
    Generates hybrid recommendations by combining content-based and collaborative filtering results
    using an interleaving strategy.
    """
    logger.info(f"Generating hybrid recommendations for user {user.username}, seed_track_id: {seed_track_id}")
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


    # Fetch a slightly larger number from each to have enough for interleaving and deduplication
    fetch_count = num_recommendations + 5

    # For content-based, we can let it fetch its default candidate set for now.
    content_recs = get_content_based_recommendations(
        user, seed_track_obj, num_recommendations=fetch_count, sp_client=sp_client
    )

    collab_recs = get_item_item_collaborative_recommendations(
        user, seed_track_obj, num_recommendations=fetch_count
    )

    # Interleaving strategy
    hybrid_recommendations = []
    # Start with seed_track_obj.id because it's a spotify_id (CharField)
    seen_track_ids = {seed_track_obj.spotify_id}

    # Add tracks already liked by the user to 'seen_track_ids' to avoid recommending them again.
    user_liked_track_spotify_ids = UserLikedTrack.objects.filter(user=user).values_list('track__spotify_id', flat=True)
    seen_track_ids.update(user_liked_track_spotify_ids)

    ptr_content, ptr_collab = 0, 0
    while len(hybrid_recommendations) < num_recommendations:
        added_in_this_iteration_flag = False # Flag to check if any new track was added in the full pass

        # Try to add from content-based
        if ptr_content < len(content_recs):
            track = content_recs[ptr_content]
            if track.spotify_id not in seen_track_ids:
                hybrid_recommendations.append(track)
                seen_track_ids.add(track.spotify_id)
                added_in_this_iteration_flag = True
            ptr_content += 1
            if len(hybrid_recommendations) >= num_recommendations: break

        # Try to add from collaborative
        if ptr_collab < len(collab_recs):
            track = collab_recs[ptr_collab]
            if track.spotify_id not in seen_track_ids:
                hybrid_recommendations.append(track)
                seen_track_ids.add(track.spotify_id)
                added_in_this_iteration_flag = True
            ptr_collab += 1
            if len(hybrid_recommendations) >= num_recommendations: break

        # If both lists are exhausted or no new unique tracks were added in the last full pass
        if not added_in_this_iteration_flag and (ptr_content >= len(content_recs) and ptr_collab >= len(collab_recs)):
            break

    logger.info(f"Hybrid recommendations for {seed_track_obj.title}: {[t.title for t in hybrid_recommendations]}")
    return hybrid_recommendations
