import json
import logging
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.views.decorators.http import require_http_methods, require_POST
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model

from music.models import Track
# Assuming get_or_create_track can fetch/create track if not locally present.
# This import path is based on previous work.
from services.spotify_service.client import get_spotify_client, get_or_create_track
from services.live_updates_service import sync_session_manager

logger = logging.getLogger(__name__)
CustomUser = get_user_model() # Already defined, just for context

@login_required
def sync_session_player_view(request):
    session_id_from_url = request.GET.get('session_id', None)
    session_data = None
    user_role = 'viewer' # Default role if just viewing or before joining

    if session_id_from_url:
        session_data = sync_session_manager.get_sync_session(session_id_from_url)
        if session_data:
            if str(request.user.id) == str(session_data.get('host_user_id')):
                user_role = 'host'
            else:
                # Check if user is already a listener to set role, otherwise they are a 'viewer' who might join
                listeners = sync_session_manager.get_session_listeners(session_id_from_url)
                if request.user.id in listeners:
                    user_role = 'listener'

            # For template display, add host username if not already in session_data (it should be)
            if 'host_username' not in session_data and session_data.get('host_user_id'):
                try:
                    host_user = CustomUser.objects.get(id=session_data.get('host_user_id'))
                    session_data['host_username'] = host_user.username
                except CustomUser.DoesNotExist:
                    session_data['host_username'] = 'Unknown Host'
        else:
            logger.warning(f"Sync session player view: Session ID {session_id_from_url} provided in URL but not found in Redis.")
            # Optionally, redirect or show a "session not found" message prominently
            # For now, template will handle "no session data" state.
            session_id_from_url = None # Clear it so join form shows

    # Prepare user data for JS
    user_data = {
        'id': request.user.id,
        'username': request.user.username,
    }

    context = {
        'session_id_from_url': session_id_from_url, # Pass the session_id from URL if present
        'session_data': session_data, # Full session data if found
        'session_data_json': json.dumps(session_data) if session_data else 'null',
        'user_data_json': json.dumps(user_data),
        'user_role': user_role, # 'host', 'listener', or 'viewer'
    }
    return render(request, 'live_sync/sync_session_player.html', context)


@login_required
@require_POST
def start_session_api(request):
    try:
        data = json.loads(request.body)
        track_spotify_id = data.get('track_spotify_id')
        if not track_spotify_id:
            return HttpResponseBadRequest(json.dumps({'error': "Missing 'track_spotify_id'."}), content_type="application/json")

        track = None
        try:
            track = Track.objects.select_related('genres').prefetch_related('artists').get(spotify_id=track_spotify_id)
        except Track.DoesNotExist:
            logger.info(f"Track {track_spotify_id} not in DB for session start. Attempting to fetch from Spotify.")
            sp = get_spotify_client(request)
            if not sp:
                 logger.error("Could not get Spotify client to fetch track details for new session.")
                 return JsonResponse({'error': 'Spotify client unavailable.'}, status=503)
            try:
                spotify_track_data = sp.track(track_spotify_id) # Fetch raw data from Spotify
                if spotify_track_data:
                    # get_or_create_track expects full track data from Spotify, not just ID for creation path
                    track = get_or_create_track(spotify_track_data, sp)
                else:
                    logger.warning(f"Track {track_spotify_id} not found on Spotify.")
                    return JsonResponse({'error': 'Track not found on Spotify.'}, status=404)
            except Exception as e:
                logger.error(f"Error fetching track {track_spotify_id} from Spotify: {e}", exc_info=True)
                return JsonResponse({'error': 'Failed to fetch track details from Spotify.'}, status=500)

        if not track:
             return JsonResponse({'error': 'Track details could not be retrieved or created.'}, status=404)

        # Ensure necessary track attributes are present
        artist_names_list = [artist.name for artist in track.artists.all()]

        session_id = sync_session_manager.create_sync_session(
            host_user_id=request.user.id,
            track_spotify_id=track.spotify_id,
            track_title=track.title or "Unknown Title",
            artist_names=", ".join(artist_names_list) or "Unknown Artist",
            album_artwork_url=track.image_url or '',
            track_duration_ms=int(track.duration.total_seconds() * 1000) if track.duration else 0
        )

        if session_id:
            return JsonResponse({'session_id': session_id, 'message': 'Sync session started successfully.'}, status=201)
        else:
            # Check if user already has an active session (create_sync_session now handles ending old one)
            # This path might be taken if create_sync_session itself had an internal error after ending old one.
            return JsonResponse({'error': 'Failed to start sync session. User might have an existing session that failed to clear, or a Redis error occurred.'}, status=500)

    except json.JSONDecodeError:
        return HttpResponseBadRequest(json.dumps({'error': "Invalid JSON."}), content_type="application/json")
    except Exception as e:
        logger.error(f"Error in start_session_api: {e}", exc_info=True)
        return JsonResponse({'error': 'An unexpected error occurred.'}, status=500)


@login_required
@require_POST
def update_host_status_api(request, session_id: str):
    try:
        data = json.loads(request.body)
        playback_status = data.get('playback_status')
        current_position_ms = data.get('current_position_ms')

        if playback_status not in ['playing', 'paused']:
            return HttpResponseBadRequest(json.dumps({'error': "Invalid 'playback_status'. Must be 'playing' or 'paused'."}), content_type="application/json")
        if not isinstance(current_position_ms, int) or current_position_ms < 0:
            return HttpResponseBadRequest(json.dumps({'error': "Invalid 'current_position_ms'. Must be a non-negative integer."}), content_type="application/json")

        success = sync_session_manager.update_sync_session_host_status(
            session_id=session_id,
            host_user_id=request.user.id,
            playback_status=playback_status,
            current_position_ms=current_position_ms
        )

        if success:
            # Placeholder for Celery task to notify listeners
            # from live_sync.tasks import broadcast_session_update
            # broadcast_session_update.delay(session_id, playback_status, current_position_ms, time.time())
            logger.info(f"Host status updated for session {session_id}. Conceptual: Dispatch Celery task for fanout.")
            return JsonResponse({'message': 'Host status updated.'})
        else:
            session_data = sync_session_manager.get_sync_session(session_id)
            if not session_data:
                return JsonResponse({'error': 'Sync session not found or expired.'}, status=404)
            return JsonResponse({'error': 'Failed to update status (e.g., not host or session error).'}, status=403)

    except json.JSONDecodeError:
        return HttpResponseBadRequest(json.dumps({'error': "Invalid JSON."}), content_type="application/json")
    except Exception as e:
        logger.error(f"Error in update_host_status_api for session {session_id}: {e}", exc_info=True)
        return JsonResponse({'error': 'An unexpected error occurred.'}, status=500)


@login_required
@require_http_methods(["GET"])
def get_session_status_api(request, session_id: str):
    session_data = sync_session_manager.get_sync_session(session_id)
    if session_data:
        # Check if user is a listener or host to allow access
        listener_ids = sync_session_manager.get_session_listeners(session_id)
        is_host = (str(request.user.id) == str(session_data.get('host_user_id')))

        if not (request.user.id in listener_ids or is_host):
            # Allow public sessions to be viewed by any authenticated user
            # if not session_data.get('is_public', False): # Assuming an 'is_public' field in session_data
            # For now, let's restrict to listeners/host only
            logger.warning(f"User {request.user.id} attempted to get status for session {session_id} they are not part of.")
            return JsonResponse({'error': 'Not part of this sync session.'}, status=403)

        return JsonResponse(session_data)
    else:
        return JsonResponse({'error': 'Sync session not found or expired.'}, status=404)


@login_required
@require_POST
def join_session_api(request, session_id: str):
    success = sync_session_manager.add_listener_to_session(session_id, request.user.id)
    if success:
        # Placeholder for Celery task
        # from live_sync.tasks import broadcast_listener_join
        # broadcast_listener_join.delay(session_id, request.user.id, request.user.username)
        logger.info(f"User {request.user.id} joined session {session_id}. Conceptual: Dispatch Celery task for fanout.")
        return JsonResponse({'message': 'Successfully joined sync session.'})
    else:
        # Check if session exists to give a more specific error
        if not sync_session_manager.get_sync_session(session_id):
            return JsonResponse({'error': 'Sync session not found or expired.'}, status=404)
        return JsonResponse({'error': 'Failed to join sync session.'}, status=500)


@login_required
@require_POST
def leave_session_api(request, session_id: str):
    session_data = sync_session_manager.get_sync_session(session_id)
    if not session_data:
        # If session doesn't exist, user effectively "left" a non-existent session.
        # Or could be an error if they thought they were in it.
        logger.info(f"User {request.user.id} attempted to leave non-existent session {session_id}.")
        return JsonResponse({'message': 'Session not found or already ended.'}, status=404)

    is_host = (str(request.user.id) == str(session_data.get('host_user_id')))

    if is_host:
        ended = sync_session_manager.end_sync_session(session_id, host_user_id_to_verify=request.user.id)
        if ended:
            # Placeholder for Celery task
            # from live_sync.tasks import broadcast_session_ended
            # broadcast_session_ended.delay(session_id, request.user.username, is_host=True)
            logger.info(f"Host {request.user.id} ended session {session_id}. Conceptual: Dispatch Celery task for fanout.")
            return JsonResponse({'message': 'Sync session ended as host left.'})
        else:
            return JsonResponse({'error': 'Failed to end sync session as host.'}, status=500)
    else:
        removed = sync_session_manager.remove_listener_from_session(session_id, request.user.id)
        if removed:
            # Placeholder for Celery task
            # from live_sync.tasks import broadcast_listener_left
            # broadcast_listener_left.delay(session_id, request.user.id, request.user.username)
            logger.info(f"User {request.user.id} left session {session_id}. Conceptual: Dispatch Celery task for fanout.")
            return JsonResponse({'message': 'Successfully left sync session.'})
        else:
            # srem doesn't error if member not in set, just returns 0.
            # So, this path means Redis error, or they weren't a listener.
            logger.warning(f"User {request.user.id} attempt to leave session {session_id}, but srem failed or user was not in set.")
            return JsonResponse({'message': 'Processed leave request (user may not have been in session or Redis error).'})

    # Fallback, should ideally not be reached if logic above is complete.
    # return JsonResponse({'error': 'An unexpected error occurred during leave.'}, status=500)
