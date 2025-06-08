from celery import shared_task
from django.contrib.auth import get_user_model
# Import get_or_create_track and the conceptual get_spotify_client_for_user
from .client import get_or_create_track, get_spotify_client_for_user
# To define UserLikedTrack, we'd import it from music.models, but we can't create it now.
# from music.models import Track, UserLikedTrack
from music.models import Track # Assuming Track model is stable

import logging

logger = logging.getLogger(__name__)

# Note: The UserLikedTrack model and CustomUser token fields cannot be created due to migration issues.
# Note: The UserLikedTrack model and CustomUser token fields can now be used as migrations are unblocked.

@shared_task(bind=True, max_retries=3, default_retry_delay=300) # Added bind=True and retry options
def sync_user_spotify_data(self, user_id): # Added self for bind=True
    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
        logger.info(f"Starting Spotify Liked Songs synchronization for user: {user.username} (ID: {user_id})")

        sp = get_spotify_client_for_user(user.id) # Uses the updated function

        if not sp:
            logger.warning(f"Could not obtain Spotify client for user {user.username}. Skipping liked songs sync.")
            return f"Skipped liked songs sync for user {user.username}: Spotify client unavailable."

        # Sync Liked Songs
        try:
            logger.info(f"Fetching saved tracks from Spotify for user {user.username}...")

            # Paginate through all liked songs
            all_spotify_liked_tracks_data = []
            offset = 0
            limit = 50
            while True:
                results = sp.current_user_saved_tracks(limit=limit, offset=offset)
                if not results or not results['items']:
                    break
                all_spotify_liked_tracks_data.extend(results['items'])
                if results['next']:
                    offset += limit
                else:
                    break

            spotify_liked_track_spotify_ids = set()
            tracks_to_create_like_for = []

            for item in all_spotify_liked_tracks_data:
                spotify_track_data = item.get('track')
                if spotify_track_data and spotify_track_data.get('id'):
                    track_instance = get_or_create_track(spotify_track_data, sp) # Ensure track is in our DB
                    if track_instance:
                        spotify_liked_track_spotify_ids.add(track_instance.spotify_id)
                        # Prepare for bulk creation or get_or_create
                        tracks_to_create_like_for.append(track_instance)

            # Bulk create UserLikedTrack entries for new likes, ignoring conflicts for existing ones
            # This assumes UserLikedTrack model is now migrated.
            from music.models import UserLikedTrack # Moved import here
            from django.utils import timezone # Moved import here

            existing_liked_track_spotify_ids = set(
                UserLikedTrack.objects.filter(user=user).values_list('track__spotify_id', flat=True)
            )

            newly_liked_tracks = []
            for track_instance in tracks_to_create_like_for:
                if track_instance.spotify_id not in existing_liked_track_spotify_ids:
                    # Note: item.get('added_at') would require finding the original item again.
                    # For simplicity, using timezone.now() for newly added liked tracks.
                    # A more complex approach could map back to `item['added_at']`.
                    newly_liked_tracks.append(
                        UserLikedTrack(user=user, track=track_instance, liked_at=timezone.now())
                    )

            if newly_liked_tracks:
                UserLikedTrack.objects.bulk_create(newly_liked_tracks, ignore_conflicts=True)
                logger.info(f"Added {len(newly_liked_tracks)} new liked tracks for user {user.username}.")

            # Remove tracks no longer liked on Spotify
            tracks_to_unlike_spotify_ids = existing_liked_track_spotify_ids - spotify_liked_track_spotify_ids
            if tracks_to_unlike_spotify_ids:
                tracks_to_delete = UserLikedTrack.objects.filter(
                    user=user,
                    track__spotify_id__in=tracks_to_unlike_spotify_ids
                )
                deleted_count, _ = tracks_to_delete.delete()
                logger.info(f"Removed {deleted_count} unliked tracks for user {user.username}.")

            logger.info(f"Processed {len(all_spotify_liked_tracks_data)} liked tracks from Spotify for {user.username}.")

        except Exception as e:
            logger.error(f"Error syncing liked songs for user {user.username}: {str(e)}", exc_info=True)
            raise self.retry(exc=e) # Retry on exception

        logger.info(f"Spotify Liked Songs synchronization task completed for user: {user.username}.")
        return f"Liked songs sync completed for {user.username}."

    except User.DoesNotExist:
        logger.error(f"User with ID {user_id} not found for Spotify data sync.")
    except Exception as e:
        logger.error(f"An unexpected error occurred in sync_user_spotify_data for user_id {user_id}: {str(e)}", exc_info=True)
        raise self.retry(exc=e) # Retry on unexpected error


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def update_user_spotify_top_items(self, user_id):
    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
        logger.info(f"Starting Spotify Top Items update for user: {user.username} (ID: {user_id})")

        sp = get_spotify_client_for_user(user.id)
        if not sp:
            logger.warning(f"Could not obtain Spotify client for user {user.username}. Skipping top items update.")
            return f"Skipped top items update for user {user.username}: Spotify client unavailable."

        # Fetch and store Top Artists
        top_artists_data = []
        try:
            top_artists_results = sp.current_user_top_artists(limit=20, time_range='medium_term') # Get more for variety
            if top_artists_results and top_artists_results['items']:
                for artist_item in top_artists_results['items']:
                    top_artists_data.append({
                        'id': artist_item.get('id'),
                        'name': artist_item.get('name'),
                        'genres': artist_item.get('genres', []),
                        'image_url': artist_item['images'][0]['url'] if artist_item.get('images') else None
                    })
            user.spotify_top_artists = top_artists_data
            logger.info(f"Updated top artists for user {user.username}")
        except Exception as e:
            logger.error(f"Error fetching top artists for user {user.username}: {str(e)}", exc_info=True)
            # Continue to tracks even if artists fail

        # Fetch and store Top Tracks
        top_tracks_data = []
        try:
            top_tracks_results = sp.current_user_top_tracks(limit=20, time_range='medium_term')
            if top_tracks_results and top_tracks_results['items']:
                for track_item in top_tracks_results['items']:
                    top_tracks_data.append({
                        'id': track_item.get('id'),
                        'name': track_item.get('name'),
                        'artist_names': [artist['name'] for artist in track_item.get('artists', [])],
                        'album_name': track_item.get('album', {}).get('name'),
                        'image_url': track_item.get('album',{}).get('images',[{}])[0].get('url') if track_item.get('album',{}).get('images') else None
                    })
            user.spotify_top_tracks = top_tracks_data
            logger.info(f"Updated top tracks for user {user.username}")
        except Exception as e:
            logger.error(f"Error fetching top tracks for user {user.username}: {str(e)}", exc_info=True)

        user.save(update_fields=['spotify_top_artists', 'spotify_top_tracks'])
        logger.info(f"Spotify Top Items update task completed for user: {user.username}.")
        return f"Top items update completed for {user.username}."

    except User.DoesNotExist:
        logger.error(f"User with ID {user_id} not found for Spotify top items update.")
    except Exception as e:
        logger.error(f"An unexpected error occurred in update_user_spotify_top_items for user_id {user_id}: {str(e)}", exc_info=True)
        raise self.retry(exc=e)


@shared_task
def sync_all_users_spotify_data(): # This will now only handle liked songs sync
    User = get_user_model()
    # Filter for users who have a spotify_refresh_token, indicating they've linked their account
    users_with_spotify = User.objects.filter(spotify_refresh_token__isnull=False)

    logger.info(f"Starting periodic Spotify Liked Songs sync for {users_with_spotify.count()} users.")
    for user in users_with_spotify:
        logger.info(f"Queueing Spotify Liked Songs sync for user: {user.username} (ID: {user.id})")
        sync_user_spotify_data.delay(user.id)
    logger.info("Completed queueing all users for Spotify Liked Songs sync.")


@shared_task
def update_all_users_spotify_top_items():
    User = get_user_model()
    users_with_spotify = User.objects.filter(spotify_refresh_token__isnull=False)

    logger.info(f"Starting periodic Spotify Top Items update for {users_with_spotify.count()} users.")
    for user in users_with_spotify:
        logger.info(f"Queueing Spotify Top Items update for user: {user.username} (ID: {user.id})")
        update_user_spotify_top_items.delay(user.id)
    logger.info("Completed queueing all users for Spotify Top Items update.")
