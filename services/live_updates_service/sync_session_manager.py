import uuid
import time
import json # May be needed for more complex data structures if not using simple strings in hmset
import logging
from django_redis import get_redis_connection

logger = logging.getLogger(__name__)

# --- Key Prefixes & Expiries ---
SYNC_SESSION_KEY_PREFIX = "sync_session:"
LISTENERS_SET_KEY_PREFIX = "sync_session_listeners:"
ACTIVE_HOST_SESSION_KEY_PREFIX = "user_active_host_session:" # Stores session_id for a host_user_id
DEFAULT_SESSION_EXPIRY_SECONDS = 3 * 60 * 60  # 3 hours (session data and listeners set)
ACTIVE_HOST_KEY_EXPIRY_SECONDS = DEFAULT_SESSION_EXPIRY_SECONDS # Make it same as session for consistency

def _decode_redis_hash(redis_hash: dict) -> dict:
    """Decodes a hash retrieved from Redis (keys and values are bytes)."""
    decoded = {}
    for key, value in redis_hash.items():
        decoded_key = key.decode('utf-8')
        decoded_value = value.decode('utf-8')
        # Attempt to convert to numbers or boolean where appropriate
        if decoded_value.isdigit():
            decoded[decoded_key] = int(decoded_value)
        elif decoded_value.replace('.', '', 1).isdigit(): # handles floats
            try:
                decoded[decoded_key] = float(decoded_value)
            except ValueError: # Not a float, keep as string
                decoded[decoded_key] = decoded_value
        elif decoded_value in ['True', 'False']:
            decoded[decoded_key] = decoded_value == 'True'
        else:
            decoded[decoded_key] = decoded_value
    return decoded

def create_sync_session(host_user_id: int, track_spotify_id: str, track_title: str,
                        artist_names: str, album_artwork_url: str, track_duration_ms: int) -> str | None:
    """
    Creates a new synchronized playback session in Redis.
    If the host already has an active session, it will be ended first.
    """
    session_id = uuid.uuid4().hex
    try:
        redis_conn = get_redis_connection("default")
        session_key = f"{SYNC_SESSION_KEY_PREFIX}{session_id}"
        listeners_key = f"{LISTENERS_SET_KEY_PREFIX}{session_id}"
        active_host_key = f"{ACTIVE_HOST_SESSION_KEY_PREFIX}{host_user_id}"

        # Check and end any existing session hosted by this user
        existing_session_id_bytes = redis_conn.get(active_host_key)
        if existing_session_id_bytes:
            old_session_id = existing_session_id_bytes.decode('utf-8')
            logger.info(f"Host user {host_user_id} already has active session {old_session_id}. Ending it now.")
            # Pass host_user_id for verification, though it's their own key we're clearing.
            end_sync_session(old_session_id, host_user_id_to_verify=host_user_id)

        current_timestamp = time.time()
        session_data = {
            "session_id": session_id, # Store session_id in the hash as well for convenience
            "host_user_id": str(host_user_id),
            "track_spotify_id": str(track_spotify_id),
            "track_title": str(track_title),
            "artist_names": str(artist_names),
            "album_artwork_url": str(album_artwork_url),
            "track_duration_ms": str(track_duration_ms),
            "playback_status": "paused",  # Initial status
            "current_position_ms": "0",
            "last_host_update_timestamp": str(current_timestamp),
            "created_at": str(current_timestamp),
        }

        pipe = redis_conn.pipeline()
        pipe.hmset(session_key, session_data)
        pipe.expire(session_key, DEFAULT_SESSION_EXPIRY_SECONDS)
        pipe.sadd(listeners_key, host_user_id) # Host is the first listener
        pipe.expire(listeners_key, DEFAULT_SESSION_EXPIRY_SECONDS)
        pipe.set(active_host_key, session_id, ex=ACTIVE_HOST_KEY_EXPIRY_SECONDS)
        pipe.execute()

        logger.info(f"Created sync session {session_id} hosted by user {host_user_id} for track {track_spotify_id}.")
        return session_id
    except Exception as e:
        logger.error(f"Error creating sync session for host {host_user_id}: {e}", exc_info=True)
        return None

def get_sync_session(session_id: str) -> dict | None:
    """Retrieves a sync session's data from Redis."""
    if not session_id: return None
    try:
        redis_conn = get_redis_connection("default")
        session_key = f"{SYNC_SESSION_KEY_PREFIX}{session_id}"
        session_data_bytes = redis_conn.hgetall(session_key)
        if not session_data_bytes:
            return None
        return _decode_redis_hash(session_data_bytes)
    except Exception as e:
        logger.error(f"Error retrieving sync session {session_id}: {e}", exc_info=True)
        return None

def update_sync_session_host_status(session_id: str, host_user_id: int,
                                    playback_status: str, current_position_ms: int) -> bool:
    """Updates the playback status and position for a session, verified by host_user_id."""
    if not session_id: return False
    try:
        redis_conn = get_redis_connection("default")
        session_key = f"{SYNC_SESSION_KEY_PREFIX}{session_id}"

        # Verify host
        stored_host_id_bytes = redis_conn.hget(session_key, "host_user_id")
        if not stored_host_id_bytes or int(stored_host_id_bytes.decode('utf-8')) != host_user_id:
            logger.warning(f"Unauthorized attempt to update session {session_id} by user {host_user_id}.")
            return False

        updates = {
            "playback_status": str(playback_status),
            "current_position_ms": str(current_position_ms),
            "last_host_update_timestamp": str(time.time())
        }

        pipe = redis_conn.pipeline()
        pipe.hmset(session_key, updates)
        pipe.expire(session_key, DEFAULT_SESSION_EXPIRY_SECONDS) # Refresh expiry on activity
        # Also refresh listener set expiry, as session is active
        listeners_key = f"{LISTENERS_SET_KEY_PREFIX}{session_id}"
        pipe.expire(listeners_key, DEFAULT_SESSION_EXPIRY_SECONDS)
        pipe.execute()

        logger.info(f"Updated sync session {session_id} by host {host_user_id}: status={playback_status}, pos={current_position_ms}ms.")
        return True
    except Exception as e:
        logger.error(f"Error updating sync session {session_id} by host {host_user_id}: {e}", exc_info=True)
        return False

def end_sync_session(session_id: str, host_user_id_to_verify: int | None = None) -> bool:
    """Ends a sync session, deleting its data from Redis."""
    if not session_id: return False
    try:
        redis_conn = get_redis_connection("default")
        session_key = f"{SYNC_SESSION_KEY_PREFIX}{session_id}"

        # Fetch actual host_user_id from the session to clear their active_host_key
        actual_host_id_bytes = redis_conn.hget(session_key, "host_user_id")
        actual_host_id = None
        if actual_host_id_bytes:
            actual_host_id = int(actual_host_id_bytes.decode('utf-8'))

        if host_user_id_to_verify is not None:
            if not actual_host_id or actual_host_id != host_user_id_to_verify:
                logger.warning(f"User {host_user_id_to_verify} attempted to end session {session_id} not hosted by them (actual host: {actual_host_id}).")
                return False

        listeners_key = f"{LISTENERS_SET_KEY_PREFIX}{session_id}"

        pipe = redis_conn.pipeline()
        pipe.delete(session_key)
        pipe.delete(listeners_key)
        if actual_host_id: # If we know the host, clear their active session link
            active_host_key = f"{ACTIVE_HOST_SESSION_KEY_PREFIX}{actual_host_id}"
            # Only delete if it still points to *this* session_id to avoid race conditions
            # This check-and-set is ideally atomic (Lua script), but for now, simple delete.
            # Consider a Lua script if race conditions become an issue.
            # if redis_conn.get(active_host_key) == session_id.encode('utf-8'):
            pipe.delete(active_host_key)

        pipe.execute()
        logger.info(f"Ended sync session {session_id}. Host was {actual_host_id if actual_host_id else 'unknown'}.")
        return True
    except Exception as e:
        logger.error(f"Error ending sync session {session_id}: {e}", exc_info=True)
        return False

def add_listener_to_session(session_id: str, user_id: int) -> bool:
    """Adds a user to a sync session's listener set."""
    if not session_id: return False
    try:
        redis_conn = get_redis_connection("default")
        session_key = f"{SYNC_SESSION_KEY_PREFIX}{session_id}"
        listeners_key = f"{LISTENERS_SET_KEY_PREFIX}{session_id}"

        if not redis_conn.exists(session_key):
            logger.warning(f"Attempt to add listener to non-existent session {session_id}.")
            return False # Session doesn't exist or expired

        pipe = redis_conn.pipeline()
        pipe.sadd(listeners_key, user_id)
        pipe.expire(listeners_key, DEFAULT_SESSION_EXPIRY_SECONDS) # Refresh expiry
        # Also refresh main session key expiry as there's activity
        pipe.expire(session_key, DEFAULT_SESSION_EXPIRY_SECONDS)
        pipe.execute()

        logger.info(f"Added listener {user_id} to sync session {session_id}.")
        return True
    except Exception as e:
        logger.error(f"Error adding listener {user_id} to session {session_id}: {e}", exc_info=True)
        return False

def remove_listener_from_session(session_id: str, user_id: int) -> bool:
    """Removes a user from a sync session's listener set."""
    if not session_id: return False
    try:
        redis_conn = get_redis_connection("default")
        listeners_key = f"{LISTENERS_SET_KEY_PREFIX}{session_id}"
        redis_conn.srem(listeners_key, user_id)
        # If the set becomes empty (excluding host perhaps), session could be considered for cleanup,
        # but expiry handles this eventually.
        logger.info(f"Removed listener {user_id} from sync session {session_id}.")
        return True
    except Exception as e:
        logger.error(f"Error removing listener {user_id} from session {session_id}: {e}", exc_info=True)
        return False

def get_session_listeners(session_id: str) -> list[int]:
    """Retrieves a list of user IDs listening to a sync session."""
    if not session_id: return []
    try:
        redis_conn = get_redis_connection("default")
        listeners_key = f"{LISTENERS_SET_KEY_PREFIX}{session_id}"
        listener_ids_bytes = redis_conn.smembers(listeners_key)
        return [int(uid.decode('utf-8')) for uid in listener_ids_bytes]
    except Exception as e:
        logger.error(f"Error retrieving listeners for session {session_id}: {e}", exc_info=True)
        return []

def get_active_session_for_host(host_user_id: int) -> str | None:
    """Gets the active session ID for a given host user ID."""
    if not host_user_id: return None
    try:
        redis_conn = get_redis_connection("default")
        active_host_key = f"{ACTIVE_HOST_SESSION_KEY_PREFIX}{host_user_id}"
        session_id_bytes = redis_conn.get(active_host_key)
        if session_id_bytes:
            return session_id_bytes.decode('utf-8')
        return None
    except Exception as e:
        logger.error(f"Error retrieving active session for host {host_user_id}: {e}", exc_info=True)
        return None
