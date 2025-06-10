import logging
from collections import defaultdict, Counter
from datetime import timedelta

from celery import shared_task
from django.utils import timezone
from django.contrib.auth import get_user_model

# Assuming models are in these locations. Adjust if necessary.
from music.models import UserLikedTrack # Track model might be needed if we store Track objects
from services.recommendation_service.models import UserRecommendationCache

logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3, default_retry_delay=300) # 5 min delay
def generate_batch_user_recommendations(self, num_recommendations=20, days_for_expiry=7):
    """
    Generates batch recommendations for all active users based on item-item collaborative filtering
    using co-liked tracks.
    """
    logger.info("Starting batch user recommendation generation for all users...")
    CustomUser = get_user_model()

    try:
        # 1. Data Fetching: Fetch all UserLikedTrack interactions
        logger.info("Fetching all user liked track interactions...")
        # Using values_list for efficiency. UserLikedTrack.track_id is the DB PK for the Track.
        all_liked_tracks_qs = UserLikedTrack.objects.all().values_list('user_id', 'track_id')

        if not all_liked_tracks_qs:
            logger.info("No UserLikedTrack data found. Skipping recommendation generation.")
            return "No UserLikedTrack data available."

        # Group likes by user: user_id -> set of track_db_ids
        user_likes = defaultdict(set)
        for user_id, track_db_id in all_liked_tracks_qs:
            user_likes[user_id].add(track_db_id)

        logger.info(f"Fetched liked tracks for {len(user_likes)} users.")

        # 2. Build Item Co-occurrence Matrix: track_db_id_1 -> track_db_id_2 -> count
        logger.info("Building item co-occurrence matrix...")
        co_occurrence_matrix = defaultdict(lambda: defaultdict(int))

        for user_id, liked_track_db_ids_set in user_likes.items():
            liked_track_db_ids_list = list(liked_track_db_ids_set)
            if len(liked_track_db_ids_list) < 2:
                continue

            for i in range(len(liked_track_db_ids_list)):
                for j in range(i + 1, len(liked_track_db_ids_list)):
                    track1_db_id = liked_track_db_ids_list[i]
                    track2_db_id = liked_track_db_ids_list[j]
                    co_occurrence_matrix[track1_db_id][track2_db_id] += 1
                    co_occurrence_matrix[track2_db_id][track1_db_id] += 1

        if not co_occurrence_matrix:
            logger.info("Co-occurrence matrix is empty. Skipping recommendation generation.")
            return "Co-occurrence matrix is empty."
        logger.info("Item co-occurrence matrix built.")

        # 3. Generate Recommendations for Each User
        active_users = CustomUser.objects.filter(is_active=True)
        logger.info(f"Generating recommendations for {active_users.count()} active users.")

        recommendations_generated_count = 0
        cache_context = "item_item_collab_batch"

        for user in active_users:
            user_already_liked_db_ids = user_likes.get(user.id, set())

            if not user_already_liked_db_ids:
                logger.debug(f"User {user.username} has no liked tracks. Skipping.")
                continue

            candidate_scores = Counter() # track_db_id -> score

            for liked_track_db_id in user_already_liked_db_ids:
                for co_liked_track_db_id, score in co_occurrence_matrix.get(liked_track_db_id, {}).items():
                    if co_liked_track_db_id not in user_already_liked_db_ids:
                        candidate_scores[co_liked_track_db_id] += score

            if not candidate_scores:
                logger.debug(f"No potential recommendations for user {user.username} based on co-occurrence.")
                continue

            sorted_recommendations = candidate_scores.most_common(num_recommendations)
            top_n_recommended_track_db_ids = [track_db_id for track_db_id, score in sorted_recommendations]

            # 4. Store Recommendations in UserRecommendationCache (as Spotify IDs)
            if top_n_recommended_track_db_ids:
                expires_at_dt = timezone.now() + timedelta(days=days_for_expiry)

                from music.models import Track # Import here to avoid circularity if models.py imports tasks.py

                # Fetch Spotify IDs for the recommended DB Track IDs
                recommended_spotify_ids = list(
                    Track.objects.filter(id__in=top_n_recommended_track_db_ids)
                                 .exclude(spotify_id__isnull=True)
                                 .exclude(spotify_id__exact='')
                                 .values_list('spotify_id', flat=True)
                )

                if recommended_spotify_ids:
                    UserRecommendationCache.objects.update_or_create(
                        user=user,
                        context=cache_context,
                        defaults={
                            'recommended_track_ids': recommended_spotify_ids,
                            'generated_at': timezone.now(),
                            'expires_at': expires_at_dt
                        }
                    )
                    recommendations_generated_count += 1
                    logger.debug(f"Stored {len(recommended_spotify_ids)} recommendations for user {user.username}.")
                else:
                    logger.warning(f"Could not map any recommended DB Track IDs to Spotify IDs for user {user.username} from {top_n_recommended_track_db_ids}.")
            else:
                logger.debug(f"No new recommendations to store for user {user.username} after filtering.")

        logger.info(f"Batch recommendation generation complete. Recommendations stored/updated for {recommendations_generated_count} users.")
        return f"Batch recommendations generated for {recommendations_generated_count} users."

    except Exception as e:
        logger.error(f"Error in generate_batch_user_recommendations: {e}", exc_info=True)
        self.retry(exc=e) # Celery will retry the task
        return f"Task failed with error: {e}"

# Placeholder for other tasks that might have been in this file
@shared_task
def train_recommendation_model_placeholder(): # Name changed to avoid conflict if another task has this name
    logger.info("Placeholder: train_recommendation_model_placeholder task executed.")
    pass

from .recommender import get_hybrid_recommendations # Import the hybrid recommender
from music.models import Track # For fetching Track object
from channels.layers import get_channel_layer # For sending to WebSocket
from asgiref.sync import async_to_sync # For calling async channel_layer from sync task

@shared_task(bind=True, max_retries=2, default_retry_delay=180) # Retry after 3 mins, up to 2 times
def generate_and_send_live_recommendations(self, user_id: int, seed_track_spotify_id: str):
    """
    Generates live hybrid recommendations based on a seed track and sends them
    to the user via WebSocket through the RecommendationConsumer.
    """
    logger.info(f"LiveRec Task: Starting for user {user_id}, seed track Spotify ID {seed_track_spotify_id}.")
    CustomUser = get_user_model()

    try:
        user = CustomUser.objects.get(id=user_id)
    except CustomUser.DoesNotExist:
        logger.error(f"LiveRec Task: User {user_id} not found. Aborting.")
        return f"User {user_id} not found."

    # Seed track object is needed by get_hybrid_recommendations if it's to call content-based part
    # The get_hybrid_recommendations itself fetches the track object by spotify_id.
    # So, we only need to pass the spotify_id.

    num_live_recs = 5 # Number of live recommendations to generate and send

    try:
        recommendations = get_hybrid_recommendations(
            user=user,
            seed_track_id=seed_track_spotify_id,
            num_recommendations=num_live_recs,
            context=f"live_recs_user_{user_id}_seed_{seed_track_spotify_id}"
        )

        if not recommendations:
            logger.info(f"LiveRec Task: No recommendations generated for user {user_id} with seed {seed_track_spotify_id}.")
            # Optionally, send a message indicating no new recommendations, or just do nothing.
            # For now, do nothing if no recs.
            return f"No recommendations generated for user {user_id}."

        # Serialize recommendations for WebSocket payload
        serialized_recommendations = []
        for track in recommendations:
            if isinstance(track, Track): # Ensure it's a Track object
                serialized_recommendations.append({
                    'spotify_id': track.spotify_id,
                    'title': track.title,
                    'artists_names_str': ", ".join([a.name for a in track.artists.all()]), # Use a simple join
                    'album_name': track.album, # Assuming album is a CharField on Track model
                    'image_url': track.image_url,
                    # Add any other fields the client-side card might need
                })
            else:
                logger.warning(f"LiveRec Task: Encountered non-Track object in recommendations: {track}")

        if not serialized_recommendations:
            logger.info(f"LiveRec Task: Serialization resulted in empty list for user {user_id}.")
            return f"Serialization failed or produced no usable recommendations for user {user_id}."

        # Send to user-specific recommendation channel group
        channel_layer = get_channel_layer()
        user_recs_group_name = f"user_{user_id}_recommendations" # Must match group in RecommendationConsumer

        event_payload = {
            "type": "live.recommendation.update", # This invokes live_recommendation_update in consumer
            "recommendations": serialized_recommendations,
            "message": "Here are some fresh recommendations based on what you're listening to!" # Optional message
        }

        async_to_sync(channel_layer.group_send)(user_recs_group_name, event_payload)

        logger.info(f"LiveRec Task: Successfully sent {len(serialized_recommendations)} live recommendations to group {user_recs_group_name} for user {user_id}.")
        return f"Sent {len(serialized_recommendations)} live recommendations to user {user_id}."

    except Track.DoesNotExist: # If get_hybrid_recommendations can't find the seed track
        logger.error(f"LiveRec Task: Seed track with Spotify ID {seed_track_spotify_id} not found. Aborting for user {user_id}.")
        # No retry needed if seed track doesn't exist.
        return f"Seed track {seed_track_spotify_id} not found."
    except Exception as e:
        logger.error(f"LiveRec Task: Error generating/sending live recommendations for user {user_id}: {e}", exc_info=True)
        self.retry(exc=e) # Retry for other types of errors (e.g., API issues, temporary DB issues)
        return f"Task failed for user {user_id} with error: {e}"
