import json # For serializing complex data if needed, though hmset handles dicts of strings
import logging
from django_redis import get_redis_connection
from django.utils import timezone
from datetime import datetime

logger = logging.getLogger(__name__)

# Define a common expiry time for listening status keys in seconds (e.g., 1 hour)
STATUS_EXPIRY_SECONDS = 3600

def update_redis_listening_status(user_id: int, track_data: dict) -> bool:
    """
    Updates the user's current listening status in Redis.

    Args:
        user_id: The ID of the user.
        track_data: A dictionary containing track information:
            'track_spotify_id' (str),
            'track_title' (str),
            'artist_names' (str), # Comma-separated string of artists
            'album_artwork_url' (str),
            'status' (str, e.g., 'playing', 'paused'),
            'playback_position_ms' (int),
            'track_duration_ms' (int),
            'is_public' (bool, optional, defaults to True)

    Returns:
        True if successful, False otherwise.
    """
    if not all(k in track_data for k in ['track_spotify_id', 'track_title', 'artist_names',
                                         'album_artwork_url', 'status',
                                         'playback_position_ms', 'track_duration_ms']):
        logger.error(f"update_redis_listening_status: Missing required keys in track_data for user {user_id}.")
        return False

    try:
        redis_conn = get_redis_connection("default")
        redis_key = f"listening_status:{user_id}"

        # Prepare data for hmset. Redis stores values as strings.
        # Ensure all complex types are serialized if necessary (e.g., bools to '1'/'0')
        # For this structure, most fields are strings or numbers which hmset handles.
        # Booleans might need explicit conversion.

        status_payload = {
            'track_spotify_id': str(track_data['track_spotify_id']),
            'track_title': str(track_data['track_title']),
            'artist_names': str(track_data['artist_names']),
            'album_artwork_url': str(track_data['album_artwork_url']),
            'status': str(track_data['status']),
            'playback_position_ms': int(track_data['playback_position_ms']),
            'track_duration_ms': int(track_data['track_duration_ms']),
            'is_public': '1' if track_data.get('is_public', True) else '0', # Convert bool to '1' or '0'
            'last_updated': datetime.utcnow().isoformat() # Store as ISO 8601 string
        }

        redis_conn.hmset(redis_key, status_payload)
        redis_conn.expire(redis_key, STATUS_EXPIRY_SECONDS)

        logger.info(f"Updated listening status for user {user_id} with track {track_data['track_spotify_id']}.")
        return True
    except Exception as e:
        logger.error(f"Error updating Redis listening status for user {user_id}: {e}", exc_info=True)
        return False

def clear_redis_listening_status(user_id: int) -> bool:
    """
    Clears the user's current listening status from Redis.

    Args:
        user_id: The ID of the user.

    Returns:
        True if successful or key didn't exist, False on error.
    """
    try:
        redis_conn = get_redis_connection("default")
        redis_key = f"listening_status:{user_id}"

        result = redis_conn.delete(redis_key)
        logger.info(f"Cleared listening status for user {user_id}. Key deleted: {result > 0}.")
        return True # .delete returns number of keys deleted, 0 if key didn't exist
    except Exception as e:
        logger.error(f"Error clearing Redis listening status for user {user_id}: {e}", exc_info=True)
        return False

def get_redis_listening_status(user_id: int) -> dict | None:
    """
    Retrieves the user's current listening status from Redis.
    (This function is not required by the prompt but is useful for completeness)
    """
    try:
        redis_conn = get_redis_connection("default")
        redis_key = f"listening_status:{user_id}"

        status_payload_bytes = redis_conn.hgetall(redis_key)
        if not status_payload_bytes:
            return None

        # Convert bytes to string and then to appropriate types
        status_payload = { k.decode('utf-8'): v.decode('utf-8') for k, v in status_payload_bytes.items() }

        # Type conversions for specific fields
        if 'playback_position_ms' in status_payload:
            status_payload['playback_position_ms'] = int(status_payload['playback_position_ms'])
        if 'track_duration_ms' in status_payload:
            status_payload['track_duration_ms'] = int(status_payload['track_duration_ms'])
        if 'is_public' in status_payload:
            status_payload['is_public'] = True if status_payload['is_public'] == '1' else False

        return status_payload
    except Exception as e:
        logger.error(f"Error retrieving Redis listening status for user {user_id}: {e}", exc_info=True)
        return None

# Example usage (for testing, not for production calls from here)
# if __name__ == '__main__':
#     # This requires Django settings to be configured to run standalone.
#     # For quick testing, you might call these from a Django shell.
#     # print(update_redis_listening_status(1, {
#     #     'track_spotify_id': 'test_id123', 'track_title': 'Test Song',
#     #     'artist_names': 'Test Artist', 'album_artwork_url': 'http://example.com/art.jpg',
#     #     'status': 'playing', 'playback_position_ms': 30000, 'track_duration_ms': 180000,
#     #     'is_public': True
#     # }))
#     # print(get_redis_listening_status(1))
#     # print(clear_redis_listening_status(1))
#     # print(get_redis_listening_status(1))
    pass
