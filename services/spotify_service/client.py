import spotipy
from spotipy.oauth2 import SpotifyOAuth, SpotifyClientCredentials
from django.conf import settings
import logging
from datetime import timedelta, datetime # Added datetime
from django.utils import timezone # Added timezone
from music.models import Track, Artist, Genre # Assuming these are your models
from django.contrib.auth import get_user_model # For get_spotify_client_for_user

logger = logging.getLogger(__name__)

# --- Functions that likely existed and are needed ---

def get_spotify_client(request): # For request-based authentication
    cache_handler = spotipy.cache_handler.DjangoSessionCacheHandler(request)
    auth_manager = SpotifyOAuth(
        client_id=settings.SPOTIFY_CLIENT_ID,
        client_secret=settings.SPOTIFY_CLIENT_SECRET,
        redirect_uri=settings.SPOTIFY_REDIRECT_URI,
        scope=settings.SPOTIFY_SCOPE,
        cache_handler=cache_handler,
        open_browser=False
    )
    return spotipy.Spotify(auth_manager=auth_manager)

def get_spotify_client_for_user(user_id): # For Celery tasks or backend use
    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.error(f"User {user_id} not found for Spotify client.")
        return None

    spotify_access_token = getattr(user, 'spotify_access_token', None)
    spotify_refresh_token = getattr(user, 'spotify_refresh_token', None)
    spotify_token_expiry = getattr(user, 'spotify_token_expiry', None)
    spotify_scope = getattr(user, 'spotify_scope', settings.SPOTIFY_SCOPE)

    if not spotify_refresh_token:
        logger.warning(f"User {user.username} has no Spotify refresh token.")
        return None

    sp_oauth = SpotifyOAuth(
        client_id=settings.SPOTIFY_CLIENT_ID,
        client_secret=settings.SPOTIFY_CLIENT_SECRET,
        redirect_uri=settings.SPOTIFY_REDIRECT_URI,
        scope=spotify_scope,
        cache_handler=None # No session cache for backend tasks
    )

    token_is_expired = not spotify_token_expiry or timezone.now() >= spotify_token_expiry - timedelta(minutes=5)
    current_access_token = spotify_access_token

    if token_is_expired or not current_access_token:
        logger.info(f"Spotify token for {user.username} needs refresh. Refreshing...")
        try:
            new_token_info = sp_oauth.refresh_access_token(spotify_refresh_token)
            if new_token_info:
                current_access_token = new_token_info.get('access_token')
                user.spotify_access_token = current_access_token
                new_refresh_token_val = new_token_info.get('refresh_token')
                if new_refresh_token_val:
                    user.spotify_refresh_token = new_refresh_token_val

                new_expires_at_ts = new_token_info.get('expires_at')
                if new_expires_at_ts:
                    user.spotify_token_expiry = timezone.make_aware(datetime.fromtimestamp(new_expires_at_ts))

                user.spotify_scope = new_token_info.get('scope', spotify_scope)

                update_fields = ['spotify_access_token', 'spotify_token_expiry', 'spotify_scope']
                if new_refresh_token_val:
                    update_fields.append('spotify_refresh_token')
                user.save(update_fields=update_fields)
                logger.info(f"Successfully refreshed and saved Spotify token for user {user.username}.")
            else:
                logger.error(f"Refreshing token returned None for {user.username}.")
                return None
        except Exception as e:
            logger.error(f"Error refreshing token for {user.username}: {e}", exc_info=True)
            return None

    if not current_access_token:
        logger.error(f"No valid access token for {user.username} after refresh attempt.")
        return None
    return spotipy.Spotify(auth=current_access_token)

def get_or_create_track(track_data, sp):
    spotify_id = track_data.get('id')
    if not spotify_id:
        return None
    try:
        track = Track.objects.get(spotify_id=spotify_id)
        return track
    except Track.DoesNotExist:
        # Simplified creation logic for brevity in this recovery step
        new_track = Track(
            title=track_data.get('name'),
            spotify_id=spotify_id,
            album=track_data.get('album', {}).get('name'),
            # duration=timedelta(milliseconds=track_data.get('duration_ms', 0)),
            preview_url=track_data.get('preview_url'),
            image_url=track_data.get('album', {}).get('images', [{}])[0].get('url') if track_data.get('album', {}).get('images') else None,
            popularity=track_data.get('popularity', 0)
        )
        # Artist and Genre linking would go here
        new_track.save()
        # Audio features fetching would go here
        return new_track

# --- New function to be added ---
def get_spotify_client_credentials_client():
    """
    Returns a Spotipy client authenticated using the Client Credentials flow.
    This client is suitable for general catalog access, not user-specific data.
    """
    try:
        auth_manager = SpotifyClientCredentials(
            client_id=settings.SPOTIFY_CLIENT_ID,
            client_secret=settings.SPOTIFY_CLIENT_SECRET
        )
        sp = spotipy.Spotify(auth_manager=auth_manager)
        return sp
    except Exception as e:
        logger.error(f"Error creating Spotify client credentials client: {e}", exc_info=True)
        return None

# --- Test function to be added ---
def test_audio_features_endpoint():
    logger.info("Attempting to test Spotify audio_features endpoint...")
    sp_client_creds = get_spotify_client_credentials_client()
    if not sp_client_creds:
        logger.error("Failed to get Spotify client with client credentials.")
        return

    test_track_ids = [
        "0c6xIDDpzE81m2q797ordA", # Example ID 1 (Toxicity by System Of A Down)
        "6rqhFgbbKwnb9MLmUQDhG6", # Example ID 2 (Smells Like Teen Spirit by Nirvana)
        "4PTG3Z6ehGkBFwjYDepo0B"  # Example ID 3 (Bohemian Rhapsody by Queen)
    ]

    # Test with valid IDs
    try:
        logger.info(f"Fetching audio features for valid track IDs: {test_track_ids}")
        features_list = sp_client_creds.audio_features(tracks=test_track_ids)

        if features_list:
            for i, features in enumerate(features_list):
                if features:
                    logger.info(f"Audio features for track ID {test_track_ids[i]}:")
                    logger.info(f"  ID: {features.get('id')}")
                    logger.info(f"  Danceability: {features.get('danceability')}")
                    logger.info(f"  Energy: {features.get('energy')}")
                    logger.info(f"  Valence: {features.get('valence')}")
                    logger.info(f"  Tempo: {features.get('tempo')}")
                    # Log a few more key features to verify structure
                    logger.info(f"  Acousticness: {features.get('acousticness')}")
                    logger.info(f"  Instrumentalness: {features.get('instrumentalness')}")
                    logger.info(f"  Liveness: {features.get('liveness')}")
                    logger.info(f"  Loudness: {features.get('loudness')}")
                    logger.info(f"  Speechiness: {features.get('speechiness')}")

                else:
                    logger.warning(f"No audio features returned for track ID {test_track_ids[i]} (response was None for this track).")
            if all(f is not None for f in features_list):
                 logger.info("SUCCESS: Audio features received for all valid test tracks.")
            else:
                 logger.warning("PARTIAL SUCCESS: Some tracks returned None for audio features.")

        else:
            logger.warning("Call to sp.audio_features with valid IDs returned an empty list or None itself.")

    except spotipy.exceptions.SpotifyException as e:
        logger.error(f"Spotify API Exception occurred with valid IDs: {e}", exc_info=True)
        logger.error(f"HTTP Status Code: {e.http_status if hasattr(e, 'http_status') else 'N/A'}")
        logger.error(f"Spotify Error Code: {e.code if hasattr(e, 'code') else 'N/A'}")
    except Exception as e:
        logger.error(f"An unexpected error occurred with valid IDs: {e}", exc_info=True)

    # Test with an invalid/non-existent ID
    invalid_track_id = "thisIsNotARealSpotifyTrackID"
    logger.info(f"Fetching audio features for invalid track ID: {invalid_track_id}")
    try:
        invalid_features = sp_client_creds.audio_features(tracks=[invalid_track_id])
        if invalid_features and invalid_features[0] is None:
            logger.info(f"SUCCESS: Audio features for invalid track ID '{invalid_track_id}' correctly returned as [None].")
        elif not invalid_features: # Empty list
             logger.info(f"SUCCESS: Audio features for invalid track ID '{invalid_track_id}' correctly returned as an empty list.")
        else:
            logger.warning(f"Audio features for invalid track ID '{invalid_track_id}' returned unexpected data: {invalid_features}")
    except spotipy.exceptions.SpotifyException as e:
        logger.error(f"Spotify API Exception occurred with invalid ID: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"An unexpected error occurred with invalid ID: {e}", exc_info=True)

    # Test with a mix of valid and invalid
    mixed_track_ids = [test_track_ids[0], invalid_track_id, test_track_ids[1]]
    logger.info(f"Fetching audio features for mixed track IDs: {mixed_track_ids}")
    try:
        mixed_features_list = sp_client_creds.audio_features(tracks=mixed_track_ids)
        if mixed_features_list and len(mixed_features_list) == len(mixed_track_ids):
            logger.info("Mixed audio features response structure:")
            for i, features in enumerate(mixed_features_list):
                if features:
                    logger.info(f"  Track {mixed_track_ids[i]}: ID {features.get('id')}, Danceability {features.get('danceability')}")
                else:
                    logger.info(f"  Track {mixed_track_ids[i]}: None (expected for invalid ID)")
            if mixed_features_list[1] is None and mixed_features_list[0] is not None and mixed_features_list[2] is not None:
                logger.info("SUCCESS: Mixed audio features call behaved as expected (valid data for valid IDs, None for invalid).")
            else:
                logger.warning("Mixed audio features call did not return the expected structure of [features, None, features].")
        else:
            logger.warning(f"Audio features for mixed track IDs returned unexpected data structure or length: {mixed_features_list}")

    except spotipy.exceptions.SpotifyException as e:
        logger.error(f"Spotify API Exception occurred with mixed IDs: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"An unexpected error occurred with mixed IDs: {e}", exc_info=True)

# Example of how to call the test (e.g., from a management command or temporarily in a view)
# if __name__ == "__main__": # This won't run directly in Django context this way
#     # This is just for conceptual structure.
#     # To run this, you'd typically call test_audio_features_endpoint()
#     # from a Django management command or a temporary test view.
#     # Ensure Django settings are configured if running standalone.
#     # DJANGO_SETTINGS_MODULE=MeloMatch.settings django-admin shell -c "from services.spotify_service.client import test_audio_features_endpoint; test_audio_features_endpoint()"
#     pass

# --- Placeholder functions to satisfy imports from core/views.py after reset ---
# These were likely more fleshed out in the actual service module before reset.
# For now, they return minimal data to allow Django to start.

def get_user_playlists(sp, request_or_user_id):
    logger.info("Placeholder: get_user_playlists called.")
    return []

def get_user_top_tracks(sp, user_id_for_cache=None):
    logger.info("Placeholder: get_user_top_tracks called.")
    return []

def get_user_recently_played(sp, user_id_for_cache=None):
    logger.info("Placeholder: get_user_recently_played called.")
    return []

def calculate_listening_time(sp, recently_played, user_id_for_cache=None):
    logger.info("Placeholder: calculate_listening_time called.")
    return 0.0

def get_favorite_genre(sp, top_tracks, user_id_for_cache=None):
    logger.info("Placeholder: get_favorite_genre called.")
    return "Unknown"

def search_tracks(sp, query, limit=10): # Added limit to match original expected signature by music/views.py
    logger.info(f"Placeholder: search_tracks called with query '{query}', limit {limit}.")
    return []

# Placeholder for get_recommendations, as music/views.py might still try to use it
# from the old .spotify import. Actual refactored recommendations are in recommendation_service.
def get_recommendations(track_id, seed_tracks, limit=5):
    logger.info(f"Placeholder: get_recommendations (spotify_service) called for track {track_id}.")
    return []

# Placeholders for jiosaavn related functions if music/views.py imports them from here
# after the reset. Ideally, these would be in jiosaavn_service.
def search_jiosaavn(query, limit=10):
    logger.info(f"Placeholder: search_jiosaavn (spotify_service) called with query '{query}'.")
    return []

def get_track_details_jiosaavn(track_id):
    logger.info(f"Placeholder: get_track_details_jiosaavn (spotify_service) called for track_id '{track_id}'.")
    return None

# More placeholders for functions imported by music/views.py
def get_or_create_playlist(playlist_id, request_or_user, sp):
    logger.info(f"Placeholder: get_or_create_playlist called for playlist_id '{playlist_id}'.")
    # Needs to return a Playlist-like object or None
    return None

def get_playlist_tracks(sp, playlist_id):
    logger.info(f"Placeholder: get_playlist_tracks called for playlist_id '{playlist_id}'.")
    return []

def create_playlist_spotify(sp, name, description=""):
    logger.info(f"Placeholder: create_playlist_spotify called for name '{name}'.")
    # Spotify API returns playlist object dict upon creation
    return {'id': 'mock_playlist_id', 'name': name, 'description': description, 'images': []}

def add_tracks_to_playlist_spotify(sp, playlist_id, track_ids):
    logger.info(f"Placeholder: add_tracks_to_playlist_spotify called for playlist_id '{playlist_id}'.")
    pass

def delete_playlist_spotify(sp, playlist_id):
    logger.info(f"Placeholder: delete_playlist_spotify called for playlist_id '{playlist_id}'.")
    pass

def remove_tracks_from_playlist_spotify(sp, playlist_id, track_ids):
    logger.info(f"Placeholder: remove_tracks_from_playlist_spotify called for playlist_id '{playlist_id}'.")
    pass
