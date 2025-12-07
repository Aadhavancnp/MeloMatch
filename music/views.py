"""
Music views for MeloMatch.

Includes async views for API-heavy operations like search, track_detail,
and artist_detail for improved performance.
"""
import logging
from datetime import datetime

from asgiref.sync import sync_to_async
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden, Http404, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.cache import cache_page
from spotipy.exceptions import SpotifyException

from music.tasks import extract_track_features_task
from services.async_utils import (
    run_in_executor,
    gather_with_exceptions,
    async_celery_delay,
)
from services.jiosaavn_service.api_client import (
    search_songs,
    get_song_details,
    search_songs_async,
    get_song_details_async,
    find_best_matching_song_async,
)
from services.spotify_service.client import (
    get_spotify_client,
    search_tracks,
    get_or_create_track,
    get_or_create_playlist,
    get_playlist_tracks,
    get_playlist_tracks_fast,
    create_playlist_spotify,
    add_tracks_to_playlist_spotify,
    delete_playlist_spotify,
    remove_tracks_from_playlist_spotify,
    get_user_top_tracks,
    get_user_recently_played,
    get_recommendations,
    async_get_spotify_client,
    async_search_tracks,
    async_get_or_create_track,
    async_get_user_top_tracks,
    async_get_user_recently_played,
    async_get_recommendations,
    async_create_playlist_spotify,
    async_add_tracks_to_playlist_spotify,
    async_delete_playlist_spotify,
    async_remove_tracks_from_playlist_spotify,
    has_spotify_token,
)
from services.youtube_service.client import async_get_youtube_audio_url
from services.utils import convert_image_to_base64
from users.models import UserActivity
from .forms import PlaylistSettingsForm
from .models import Playlist, Track

logger = logging.getLogger(__name__)


# =============================================================================
# ASYNC VIEWS
# =============================================================================

@login_required
async def search(request):
    """
    Async search view for tracks.
    Makes async Spotify API calls for improved performance.
    """
    query = request.GET.get('q', '')
    sort = request.GET.get('sort', '')
    tracks = []

    if query:
        sp = await async_get_spotify_client(request)
        tracks = await async_search_tracks(sp, query)

        # Sort tracks if requested
        if sort == 'popularity':
            tracks = sorted(tracks, key=lambda x: x.popularity or 0, reverse=True)
        elif sort == '-popularity':
            tracks = sorted(tracks, key=lambda x: x.popularity or 0)
        elif sort == 'release_date':
            # Newest first - None dates go to end
            from datetime import date
            tracks = sorted(
                tracks,
                key=lambda x: x.release_date if x.release_date else date.min,
                reverse=True
            )
        elif sort == '-release_date':
            # Oldest first - None dates go to end
            from datetime import date
            tracks = sorted(
                tracks,
                key=lambda x: x.release_date if x.release_date else date.max
            )

        # Log user activity
        await sync_to_async(UserActivity.objects.create)(
            user=request.user,
            activity_type='search',
            description=f"Searched for: {query}"
        )

    context = {
        'query': query,
        'tracks': tracks,
        'sort': sort
    }

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({
            'query': query,
            'tracks': [{
                'id': track.spotify_id,
                'title': track.title,
                'artists': track.artists_names,
                'album': track.album,
                'release_date': track.release_date.strftime('%Y-%m-%d') if track.release_date else '',
                'popularity': track.popularity,
                'image_url': track.image_url,
                'preview_url': track.preview_url
            } for track in tracks]
        })

    return await sync_to_async(render)(request, 'music/search.html', context)


def callback(request):
    """Spotify OAuth callback."""
    code = request.GET.get('code')
    sp = get_spotify_client(request)
    token_info = sp.auth_manager.get_access_token(code)
    request.session['token_info'] = token_info
    return redirect('dashboard')


@login_required
async def track_detail(request, track_id):
    """
    Async track detail view.
    Makes parallel API calls for recommendations and track features.
    """
    # Try to get track from database first
    @sync_to_async
    def get_track():
        try:
            return Track.objects.select_related('genres').prefetch_related('artists').get(
                spotify_id=track_id
            )
        except Track.DoesNotExist:
            return None

    track = await get_track()

    # If not found locally, fetch from Spotify
    if not track:
        sp = await async_get_spotify_client(request)
        try:
            track_data = await run_in_executor(sp.track, track_id)
            track = await async_get_or_create_track(track_data, sp)
            if not track:
                raise Http404(
                    "Track not found on Spotify or could not be created.")
        except SpotifyException:
            raise Http404("Track not found on Spotify.")
        except Exception as e:
            logger.error(f"Error fetching/creating track {track_id}: {e}")
            raise Http404("Error processing track.")

    # Try to get Spotify client for additional features (recommendations)
    # but don't fail if OAuth isn't set up - use raise_on_no_token=True to avoid interactive prompts
    sp = None
    recommendations = []
    try:
        sp = await async_get_spotify_client(request, raise_on_no_token=True)
    except Exception as e:
        logger.info(
            f"Spotify client unavailable (user not authenticated): {e}")

    # Note: Spotify's audio_features API is deprecated for most apps (403 error)
    # We now use Librosa for audio feature extraction from preview files

    # Parallel API calls for user data and recommendations (only if Spotify available)
    top_tracks = []
    recently_played = []
    recommendation_data_list = []

    if sp:
        try:
            top_tracks, recently_played = await gather_with_exceptions(
                async_get_user_top_tracks(request),
                async_get_user_recently_played(request),
                return_exceptions=True
            )

            top_tracks = top_tracks if not isinstance(
                top_tracks, Exception) else []
            recently_played = recently_played if not isinstance(
                recently_played, Exception) else []

            # Get recommendations
            recommendation_data_list = await async_get_recommendations(
                sp, track.spotify_id, (top_tracks or []) + (recently_played or []), limit=5
            )
        except Exception as e:
            logger.warning(
                f"Error fetching Spotify data for track detail: {e}")

    # Fetch recommendation Track objects from database
    recommendation_spotify_ids = [
        rec['id'] for rec in recommendation_data_list
        if isinstance(rec, dict) and 'id' in rec
    ]

    @sync_to_async
    def get_recommendations_from_db():
        if not recommendation_spotify_ids:
            return []
        recommendations_qs = Track.objects.filter(
            spotify_id__in=recommendation_spotify_ids
        ).prefetch_related('artists', 'genres')
        recommendations_map = {t.spotify_id: t for t in recommendations_qs}
        return [
            recommendations_map[sid]
            for sid in recommendation_spotify_ids
            if sid in recommendations_map
        ]

    recommendations = await get_recommendations_from_db()

    # Build artist links and get user playlists in PARALLEL
    @sync_to_async
    def get_artist_links():
        return [
            {'name': artist.name.strip(), 'url': reverse(
                'artist_detail', args=[artist.name.strip()])}
            for artist in track.artists.all()
        ]

    @sync_to_async
    def get_user_playlists():
        return list(Playlist.objects.filter(user=request.user).order_by('name'))

    artists, user_playlists = await gather_with_exceptions(
        get_artist_links(),
        get_user_playlists(),
        return_exceptions=True
    )
    artists = artists if not isinstance(artists, Exception) else []
    user_playlists = user_playlists if not isinstance(user_playlists, Exception) else []

    # Helper to check if URL is a valid audio stream (not a YouTube watch page)
    def is_valid_audio_url(url: str) -> bool:
        if not url:
            return False
        # YouTube watch pages can't be played directly in HTML5 audio
        if 'youtube.com/watch' in url or 'youtu.be/' in url:
            return False
        return True

    # Fetch preview URL if missing - JioSaavn is fast (~2-3s), YouTube is slow (30s+)
    # We fetch JioSaavn synchronously for instant playback, queue YouTube as fallback
    if not track.preview_url or not is_valid_audio_url(track.preview_url):
        @sync_to_async
        def get_artist_names_list():
            return [artist.name for artist in track.artists.all()]

        artist_names = await get_artist_names_list()
        query = f"{track.title} {' '.join(artist_names)}".strip()
        preview_url = None
        
        # Get track duration in milliseconds
        track_duration_ms = int(track.duration.total_seconds() * 1000) if track.duration else 0
        
        # Try JioSaavn first (fast, ~2-3 seconds) with strict matching including duration
        try:
            jiosaavn_result = await find_best_matching_song_async(
                query=query,
                target_title=track.title,
                target_artists=artist_names,
                min_artist_similarity=0.5,
                target_duration_ms=track_duration_ms  # Pass duration for validation
            )
            if jiosaavn_result and jiosaavn_result.get('preview_url'):
                preview_url = jiosaavn_result['preview_url']
                logger.info(f"Found JioSaavn preview for '{track.title}' - matched: {jiosaavn_result.get('title')}")
        except Exception as e:
            logger.debug(f"JioSaavn search failed for '{track.title}': {e}")

        # Save preview if found from JioSaavn
        if preview_url:
            track.preview_url = preview_url
            await sync_to_async(track.save)(update_fields=['preview_url'])
            
            # Queue feature extraction in background (non-blocking)
            if not track.audio_features:
                try:
                    await async_celery_delay(extract_track_features_task, track.id, preview_url)
                except Exception:
                    pass
        else:
            # Queue YouTube fallback in background (slow ~30-60s, user can refresh)
            try:
                from music.tasks import fetch_youtube_preview_task
                await async_celery_delay(fetch_youtube_preview_task, track.id, query)
                logger.info(f"Queued YouTube fallback for '{track.title}'")
            except Exception as e:
                logger.debug(f"Failed to queue YouTube fallback: {e}")

    # Log activity and refresh track in PARALLEL (non-blocking)
    @sync_to_async
    def log_activity_and_refresh():
        # Log activity
        artist_names = ', '.join([artist.name for artist in track.artists.all()])
        UserActivity.objects.create(
            user=request.user,
            activity_type='view_track',
            description=f"Viewed track: {track.title} by {artist_names}"
        )
        # Refresh track to get any updates (e.g., audio_features from Celery)
        track.refresh_from_db()

    await log_activity_and_refresh()

    context = {
        'track': track,
        'recommendations': recommendations,
        'artists': artists,
        'playlists': user_playlists,
    }

    return render(request, 'music/track_detail.html', context)


@login_required
@cache_page(3600)
async def artist_detail(request, artist_name):
    """
    Async artist detail view.
    Makes parallel Spotify API calls for artist info, top tracks, and albums.
    """
    sp = await async_get_spotify_client(request)

    # Search for artist
    search_result = await run_in_executor(sp.search, artist_name, 1, 0, 'artist')
    if not search_result['artists']['items']:
        raise Http404("Artist not found")

    artist_id = search_result['artists']['items'][0]['id']

    # Parallel API calls for artist data
    artist, top_tracks_result, albums_result = await gather_with_exceptions(
        run_in_executor(sp.artist, artist_id),
        run_in_executor(sp.artist_top_tracks, artist_id),
        run_in_executor(sp.artist_albums, artist_id, 'album', None, 5),
        return_exceptions=True
    )

    if isinstance(artist, Exception):
        logger.error(f"Error fetching artist {artist_id}: {artist}")
        raise Http404("Error fetching artist details")

    top_tracks = top_tracks_result.get('tracks', [])[:5] if not isinstance(
        top_tracks_result, Exception) else []
    albums = albums_result.get('items', []) if not isinstance(
        albums_result, Exception) else []

    context = {
        'artist': artist,
        'top_tracks': top_tracks,
        'albums': albums,
    }

    await sync_to_async(UserActivity.objects.create)(
        user=request.user,
        activity_type='view_artist',
        description=f"Viewed artist: {artist['name']}"
    )

    return render(request, 'music/artist_detail.html', context)


# =============================================================================
# PLAYLIST VIEWS (Sync for transaction safety)
# =============================================================================

def playlist_detail(request, playlist_id):
    """Playlist detail view. Public playlists accessible without login."""
    try:
        playlist = get_object_or_404(
            Playlist.objects.select_related('user').prefetch_related(
                'tracks__artists',
                'tracks__genres',
                'shared_with'
            ),
            spotify_id=playlist_id
        )

    except Http404:
        # Can only fetch from Spotify if logged in
        if not request.user.is_authenticated:
            raise Http404("Playlist not found.")
        sp = get_spotify_client(request)
        playlist_data_from_spotify = sp.playlist(playlist_id)
        if not playlist_data_from_spotify:
            raise Http404("Playlist not found on Spotify.")

        playlist = get_or_create_playlist(playlist_id, request, sp)
        if not playlist:
            return redirect('dashboard')    # Permission check
    is_owner = request.user.is_authenticated and (
        request.user == playlist.user)
    can_view = playlist.is_public or is_owner or (
        request.user.is_authenticated and request.user in playlist.shared_with.all()
    )

    if not can_view:
        if not request.user.is_authenticated:
            # Redirect to login for private playlists
            from django.contrib.auth.views import redirect_to_login
            return redirect_to_login(request.get_full_path())
        messages.error(
            request, "You do not have permission to view this playlist.")
        return HttpResponseForbidden("You do not have permission to view this playlist.")

    # Sync tracks if owner or if needed (only if authenticated)
    if request.user.is_authenticated and (is_owner or not playlist.tracks.exists()):
        sp = get_spotify_client(request)
        if sp:
            # Use fast parallel track fetching
            spotify_tracks = get_playlist_tracks_fast(playlist.spotify_id, request, sp)
            if spotify_tracks:
                with transaction.atomic():
                    playlist.tracks.set(spotify_tracks)
                    playlist.save()

    context = {
        'playlist': playlist,
        'is_owner': is_owner,
        'can_view': can_view,
    }

    # Log activity only for authenticated users
    if request.user.is_authenticated:
        UserActivity.objects.create(
            user=request.user,
            activity_type='view_playlist',
            description=f"Viewed playlist: {playlist.name}"
        )

    return render(request, 'music/playlist_detail.html', context)


@login_required
def create_playlist(request):
    """Create new playlist."""
    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description', '')
        cover_image = request.FILES.get('cover_image')

        if name:
            sp = get_spotify_client(request)
            try:
                with transaction.atomic():
                    playlist = create_playlist_spotify(sp, name, description)

                    if cover_image:
                        base64_img = convert_image_to_base64(cover_image)
                        sp.playlist_upload_cover_image(
                            playlist['id'], base64_img)
                        playlist = sp.playlist(playlist['id'])

                    Playlist.objects.create(
                        user=request.user,
                        description=playlist['description'],
                        name=playlist['name'],
                        spotify_id=playlist['id'],
                        image_url=playlist['images'][0]['url'] if playlist['images'] else None
                    )

                    UserActivity.objects.create(
                        user=request.user,
                        activity_type='create_playlist',
                        description=f"Created playlist: {playlist['name']}"
                    )

                return redirect('playlist_detail', playlist_id=playlist['id'])
            except SpotifyException as e:
                if e.http_status == 403:
                    return redirect(sp.auth_manager.get_authorize_url())
                messages.error(request, f"Spotify error: {e}")
                return render(request, 'music/create_playlist.html')

    return render(request, 'music/create_playlist.html')


@login_required
def add_to_playlist(request):
    """Add track to playlist."""
    if request.method == 'POST':
        track_id = request.POST.get('track_id')
        playlist_id = request.POST.get('playlist_id')

        if track_id and playlist_id:
            sp = get_spotify_client(request)
            try:
                with transaction.atomic():
                    add_tracks_to_playlist_spotify(sp, playlist_id, [track_id])
                    playlist = Playlist.objects.get(
                        spotify_id=playlist_id, user=request.user)
                    song = Track.objects.get(spotify_id=track_id)
                    playlist.tracks.add(song)
                    playlist.save()

                    UserActivity.objects.create(
                        user=request.user,
                        activity_type='add_to_playlist',
                        description=f"Added song {song.title} to playlist {playlist.name}"
                    )

                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'status': 'success'})

                return redirect('playlist_detail', playlist_id=playlist_id)
            except SpotifyException as e:
                if e.http_status == 403:
                    return redirect(sp.auth_manager.get_authorize_url())
                messages.error(request, f"Spotify error: {e}")
                return redirect('playlist_detail', playlist_id=playlist_id)
            except (Playlist.DoesNotExist, Track.DoesNotExist) as e:
                logger.error(f"Database error adding track to playlist: {e}")
                return JsonResponse({'status': 'error', 'message': str(e)}, status=400)

    return JsonResponse({'status': 'error'}, status=400)


@login_required
def edit_playlist_settings(request, playlist_id):
    """Edit playlist settings."""
    playlist = get_object_or_404(Playlist, spotify_id=playlist_id)

    if request.user != playlist.user:
        messages.error(
            request, "You do not have permission to edit this playlist.")
        return HttpResponseForbidden("You cannot edit this playlist.")

    if request.method == 'POST':
        form = PlaylistSettingsForm(
            request.POST, request.FILES, instance=playlist)
        if form.is_valid():
            with transaction.atomic():
                form.save()

                sp = get_spotify_client(request)
                try:
                    sp.playlist_change_details(
                        playlist.spotify_id,
                        name=playlist.name,
                        public=playlist.is_public,
                        description=playlist.description or ""
                    )

                    cover_image = request.FILES.get('cover_image')
                    if cover_image:
                        try:
                            base64_img = convert_image_to_base64(cover_image)
                            sp.playlist_upload_cover_image(
                                playlist.spotify_id, base64_img)

                            # Wait a moment for Spotify to process the image
                            import time
                            time.sleep(2)

                            # Fetch updated playlist data from Spotify multiple times if needed
                            for attempt in range(3):
                                updated_playlist = sp.playlist(
                                    playlist.spotify_id)
                                if updated_playlist.get('images'):
                                    new_url = updated_playlist['images'][0]['url']
                                    if new_url != playlist.image_url:
                                        playlist.image_url = new_url
                                        playlist.save(
                                            update_fields=['image_url'])
                                        break
                                time.sleep(1)
                        except Exception as e:
                            messages.warning(
                                request,
                                f"Playlist updated but cover image upload failed: {str(e)}"
                            )

                    messages.success(
                        request, "Playlist settings updated successfully.")
                    return redirect('playlist_detail', playlist_id=playlist.spotify_id)
                except SpotifyException as e:
                    if e.http_status == 403:
                        return redirect(sp.auth_manager.get_authorize_url())
                    messages.error(request, f"Spotify error: {e}")
                    return redirect('playlist_detail', playlist_id=playlist.spotify_id)
    else:
        form = PlaylistSettingsForm(instance=playlist)

    context = {
        'form': form,
        'playlist': playlist,
    }
    return render(request, 'music/edit_playlist_settings.html', context)


@login_required
def delete_playlist(request, playlist_id):
    """Delete playlist."""
    playlist = get_object_or_404(Playlist, spotify_id=playlist_id)

    if request.user != playlist.user:
        return HttpResponseForbidden("You don't have permission to delete this playlist.")

    if request.method == 'POST':
        sp = get_spotify_client(request)
        try:
            with transaction.atomic():
                delete_playlist_spotify(sp, playlist_id=playlist_id)
                playlist.delete()

            messages.success(request, 'Playlist deleted successfully.')
            return redirect('dashboard')
        except SpotifyException as e:
            if e.http_status == 403:
                return redirect(sp.auth_manager.get_authorize_url())
            messages.error(request, f"Spotify error: {e}")
            return redirect('playlist_detail', playlist_id=playlist_id)

    return HttpResponseForbidden("Invalid request method.")


@login_required
def delete_track(request, playlist_id, track_id):
    """Remove track from playlist."""
    playlist = get_object_or_404(Playlist, spotify_id=playlist_id)
    track = get_object_or_404(Track, spotify_id=track_id)

    if request.user != playlist.user:
        return HttpResponseForbidden("You don't have permission to modify this playlist.")

    if request.method == 'DELETE':
        sp = get_spotify_client(request)
        try:
            with transaction.atomic():
                remove_tracks_from_playlist_spotify(
                    sp, playlist_id, [track_id])
                playlist.tracks.remove(track)

            messages.success(
                request, 'Track removed from playlist successfully.')
            return redirect('playlist_detail', playlist_id=playlist_id)
        except SpotifyException as e:
            if e.http_status == 403:
                return redirect(sp.auth_manager.get_authorize_url())
            messages.error(request, f"Spotify error: {e}")
            return redirect('playlist_detail', playlist_id=playlist_id)

    return HttpResponseForbidden("Invalid request method.")
