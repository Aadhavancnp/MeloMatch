import logging
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from django.db.models import Q

from music.models import Track, UserLikedTrack # Assuming UserLikedTrack might be used for filtering later
from .utils import _get_standardized_features_for_track # Key function for content-based
from .models import UserRecommendationCache # For hybrid or future caching here
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger(__name__)

DEFAULT_CANDIDATE_POOL_SIZE = 200 # Number of candidates to consider if no genre filter
GENRE_FILTERED_CANDIDATE_POOL_SIZE = 100 # Number of candidates if filtering by genre

def get_content_based_recommendations(user, seed_track_obj: Track, num_recommendations: int = 10, candidate_pool_size: int = None):
    """
    Generates content-based recommendations for a user based on a seed track.
    Uses Librosa-extracted features and cosine similarity.
    Includes genre-based candidate pre-filtering.
    """
    if not isinstance(seed_track_obj, Track):
        logger.error("get_content_based_recommendations: seed_track_obj is not a Track instance.")
        return []

    logger.info(f"Generating content-based recommendations for user {user.username} based on seed track '{seed_track_obj.title}' (ID: {seed_track_obj.id})")

    seed_features_vector = _get_standardized_features_for_track(seed_track_obj)

    if seed_features_vector is None or np.all(seed_features_vector == 0): # Check if all zeros
        logger.warning(f"Could not get valid features for seed track {seed_track_obj.id}. Cannot generate content-based recommendations.")
        return []

    # Candidate Track Selection with Genre Pre-filtering
    candidate_tracks_qs = Track.objects.exclude(id=seed_track_obj.id).exclude(audio_features__isnull=True) # Exclude tracks with no features yet

    # Genre pre-filtering
    seed_genre = seed_track_obj.genres # Accesses the ForeignKey to Genre
    if seed_genre:
        logger.info(f"Seed track genre: {seed_genre.name}. Filtering candidates by this genre.")
        candidate_tracks_qs = candidate_tracks_qs.filter(genres=seed_genre)
        actual_candidate_pool_size = candidate_pool_size or GENRE_FILTERED_CANDIDATE_POOL_SIZE
    else:
        logger.info("Seed track has no primary genre. Using a general pool of candidates.")
        actual_candidate_pool_size = candidate_pool_size or DEFAULT_CANDIDATE_POOL_SIZE

    # Further filtering (e.g., exclude already liked by user, if UserLikedTrack is available and desired here)
    try:
        user_liked_track_ids = UserLikedTrack.objects.filter(user=user).values_list('track_id', flat=True)
        candidate_tracks_qs = candidate_tracks_qs.exclude(id__in=user_liked_track_ids)
        logger.info(f"Excluding {len(user_liked_track_ids)} tracks already liked by user {user.username}.")
    except Exception as e:
        # This can happen if UserLikedTrack table doesn't exist due to migration issues.
        logger.error(f"Could not query UserLikedTrack for exclusions: {e}. Proceeding without this filter.")


    candidate_tracks = list(candidate_tracks_qs.prefetch_related('artists', 'genres')[:actual_candidate_pool_size])

    if not candidate_tracks:
        logger.warning(f"No candidate tracks found for seed {seed_track_obj.id} after filtering.")
        return []

    logger.info(f"Processing {len(candidate_tracks)} candidate tracks for content-based similarity.")

    candidate_features_list = []
    valid_candidates = []

    for candidate_track in candidate_tracks:
        candidate_feature_vector = _get_standardized_features_for_track(candidate_track)
        if candidate_feature_vector is not None and not np.all(candidate_feature_vector == 0):
            candidate_features_list.append(candidate_feature_vector)
            valid_candidates.append(candidate_track)
        else:
            logger.debug(f"Skipping candidate track {candidate_track.id} due to missing or zero features.")

    if not valid_candidates or not candidate_features_list:
        logger.warning(f"No valid feature vectors for any candidate tracks for seed {seed_track_obj.id}.")
        return []

    # Calculate Cosine Similarities
    # Ensure seed_features_vector is 2D for cosine_similarity
    seed_features_vector_2d = seed_features_vector.reshape(1, -1)
    candidate_features_matrix = np.array(candidate_features_list)

    try:
        similarities = cosine_similarity(seed_features_vector_2d, candidate_features_matrix)
    except Exception as e:
        logger.error(f"Error calculating cosine similarity: {e}", exc_info=True)
        return []

    if similarities.size == 0:
        logger.warning("Cosine similarity calculation resulted in an empty array.")
        return []

    # Get top N recommendations
    similarity_scores = similarities[0] # Similarities is [[s1, s2, ...]]

    # Combine candidates with their scores and sort
    scored_candidates = sorted(zip(valid_candidates, similarity_scores), key=lambda x: x[1], reverse=True)

    top_n_recommendations = [track for track, score in scored_candidates[:num_recommendations]]

    logger.info(f"Generated {len(top_n_recommendations)} content-based recommendations for seed track {seed_track_obj.id}.")
    return top_n_recommendations


def get_item_item_collaborative_recommendations(user, seed_track_obj: Track, num_recommendations: int = 10):
    """
    Placeholder for item-item collaborative filtering based on a seed track.
    This would typically look for users who liked the seed_track_obj, then find other tracks those users liked.
    """
    logger.info(f"Placeholder: Item-item collaborative filtering called for user {user.username}, seed track {seed_track_obj.title}")
    # Actual implementation would involve co-occurrence matrices or similar logic from UserLikedTrack data.
    # For now, return empty list.
    return []


def get_hybrid_recommendations(user, seed_track_id: str, num_recommendations: int = 10, context: str = "hybrid_general"):
    """
    Generates hybrid recommendations by combining content-based and collaborative filtering.
    Caches the results.
    """
    logger.info(f"Hybrid recommendations requested for user {user.username}, seed_track_id: {seed_track_id}, context: {context}")

    # Check cache first
    cache_key = f"hybrid_recs_user_{user.id}_seed_{seed_track_id}_ctx_{context}"
    cached_recs_data = UserRecommendationCache.objects.filter(user=user, context=cache_key).first()

    if cached_recs_data and not cached_recs_data.is_expired():
        logger.info(f"Returning cached hybrid recommendations for key: {cache_key}")
        # recommended_track_ids are Spotify IDs. Need to fetch Track objects.
        # This part assumes Track.spotify_id is unique and indexed.
        spotify_ids = cached_recs_data.recommended_track_ids
        # Fetch tracks and maintain order
        tracks_qs = Track.objects.filter(spotify_id__in=spotify_ids).prefetch_related('artists', 'genres')
        tracks_map = {track.spotify_id: track for track in tracks_qs}
        ordered_tracks = [tracks_map[spotify_id] for spotify_id in spotify_ids if spotify_id in tracks_map]
        return ordered_tracks

    logger.info(f"No valid cache found for hybrid recommendations (key: {cache_key}). Generating fresh recommendations.")

    try:
        seed_track_obj = Track.objects.get(spotify_id=seed_track_id)
    except Track.DoesNotExist:
        logger.error(f"Seed track with Spotify ID {seed_track_id} not found in DB for hybrid recommendations.")
        return []
    except Exception as e: # Handle other potential errors like multiple objects if spotify_id isn't unique
        logger.error(f"Error fetching seed track {seed_track_id}: {e}", exc_info=True)
        return []

    # 1. Get Content-Based Recommendations
    content_based_recs = get_content_based_recommendations(user, seed_track_obj, num_recommendations=num_recommendations + 5) # Get a few extra

    # 2. Get Collaborative Filtering Recommendations (Item-Item based on seed track)
    # This is a placeholder, as the full user-item or item-item matrix might not be readily available for one seed.
    # The batch item-item CF task calculates global co-occurrences. A real-time version might be different.
    collab_recs = get_item_item_collaborative_recommendations(user, seed_track_obj, num_recommendations=num_recommendations + 5)

    # 3. Combine and Rank (Simple Interleaving/Weighted Approach for now)
    # This is a very basic combination strategy. More sophisticated methods exist (e.g., weighted, switching, cascade).

    final_recommendations = []
    # Initialize seen_track_ids with the seed track and tracks already liked by the user.
    # Using DB IDs for seen_track_ids as Track objects have .id
    seen_track_ids = {seed_track_obj.id}
    try:
        user_liked_track_db_ids = UserLikedTrack.objects.filter(user=user).values_list('track_id', flat=True)
        seen_track_ids.update(user_liked_track_db_ids)
        logger.info(f"get_hybrid_recommendations: Initial seen_track_ids count for user {user.username}: {len(seen_track_ids)}")
    except Exception as e:
        logger.error(f"get_hybrid_recommendations: Could not query UserLikedTrack for initial exclusions: {e}. Proceeding without.")

    # Refined Interleaving Strategy
    cb_ptr = 0
    collab_ptr = 0

    # Fetch more from sub-recommenders to ensure enough unique items after filtering
    # The sub-recommenders already fetch num_recommendations + 5

    while len(final_recommendations) < num_recommendations:
        added_this_round = False

        # Try from content-based
        if cb_ptr < len(content_based_recs):
            cb_track = content_based_recs[cb_ptr]
            if cb_track.id not in seen_track_ids:
                final_recommendations.append(cb_track)
                seen_track_ids.add(cb_track.id)
                if len(final_recommendations) == num_recommendations:
                    break
            added_this_round = True # Mark that we processed an item, even if duplicate
            cb_ptr += 1

        # Try from collaborative
        if collab_ptr < len(collab_recs):
            collab_track = collab_recs[collab_ptr]
            if collab_track.id not in seen_track_ids:
                final_recommendations.append(collab_track)
                seen_track_ids.add(collab_track.id)
                if len(final_recommendations) == num_recommendations:
                    break
            added_this_round = True # Mark that we processed an item
            collab_ptr += 1

        # Break if both lists are exhausted or if we are not adding new unique items
        if not added_this_round and (cb_ptr >= len(content_based_recs) and collab_ptr >= len(collab_recs)):
            logger.info("Both recommendation sources exhausted or no new unique items to add.")
            break
        # Safety break if lists are very long but full of duplicates and we are stuck
        if cb_ptr >= len(content_based_recs) and collab_ptr >= len(collab_recs) and not added_this_round :
             logger.warning("Interleaving stuck due to all remaining items being duplicates or already seen.")
             break


    # final_recommendations list is already sliced implicitly by the loop condition.
    # No need for final_recommendations = final_recommendations[:num_recommendations] here.

    # Save to cache
    if final_recommendations:
        recommended_spotify_ids_to_cache = [track.spotify_id for track in final_recommendations if track.spotify_id]
        if recommended_spotify_ids_to_cache:
            expires_at_dt = timezone.now() + timedelta(hours=12) # Cache hybrid recs for 12 hours
            UserRecommendationCache.objects.update_or_create(
                user=user,
                context=cache_key, # Use the detailed cache key
                defaults={
                    'recommended_track_ids': recommended_spotify_ids_to_cache,
                    'generated_at': timezone.now(),
                    'expires_at': expires_at_dt
                }
            )
            logger.info(f"Saved {len(recommended_spotify_ids_to_cache)} hybrid recommendations to cache for key: {cache_key}")

    logger.info(f"Generated {len(final_recommendations)} hybrid recommendations for user {user.username}, seed track {seed_track_id}.")
    return final_recommendations
