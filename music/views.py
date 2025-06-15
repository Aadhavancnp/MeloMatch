from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.cache import cache_page
from django.contrib.auth import get_user_model # Added for user checking

from users.models import UserActivity, CustomUser # Added CustomUser
from .models import Playlist, Track
from .forms import PlaylistSettingsForm # Import the actual form
from .spotify import get_recommendations, get_spotify_client, \
    search_jiosaavn, get_track_details_jiosaavn, get_user_top_tracks, get_user_recently_played, create_playlist_spotify, \
    search_tracks, get_or_create_playlist, get_playlist_tracks, \
    add_tracks_to_playlist_spotify, delete_playlist_spotify, remove_tracks_from_playlist_spotify
    # Removed extract_audio_features, download_preview from this import
from .utils import convert_image_to_base64
from .tasks import extract_track_features_task # Import the Celery task


@login_required
def search(request):
    query = request.GET.get('q', '')
    sort = request.GET.get('sort', '')
    tracks = []
    if query:
        sp = get_spotify_client(request)
        tracks = search_tracks(sp, query)

        if sort == 'popularity':
            tracks = sorted(tracks, key=lambda x: x.popularity, reverse=True)
        elif sort == '-popularity':
            tracks = sorted(tracks, key=lambda x: x.popularity)
        elif sort == 'release_date':
            tracks = sorted(tracks, key=lambda x: x.release_date.date() if isinstance(x.release_date,
                                                                                      datetime) else x.release_date,
                            reverse=True)
        elif sort == '-release_date':
            tracks = sorted(tracks, key=lambda x: x.release_date.date() if isinstance(x.release_date,
                                                                                      datetime) else x.release_date)
        UserActivity.objects.create(
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
                'release_date': track.release_date.strftime('%Y-%m-%d'),
                'popularity': track.popularity,
                'image_url': track.image_url,
                'preview_url': track.preview_url
            } for track in tracks]
        })

    return render(request, 'music/search.html', context)


def callback(request):
    code = request.GET.get('code')
    sp = get_spotify_client(request)
    token_info = sp.auth_manager.get_access_token(code)
    request.session['token_info'] = token_info
    return redirect('dashboard')


@login_required
@cache_page(3600)
def track_detail(request, track_id):
    # Optimized query for the main track
    track = get_object_or_404(
        Track.objects.select_related('genres').prefetch_related('artists'),
        spotify_id=track_id
    )
    sp = get_spotify_client(request)
    top_tracks = get_user_top_tracks(sp) # Returns list of dicts
    recently_played = get_user_recently_played(sp) # Returns list of dicts

    # get_recommendations returns a list of dicts {'id': spotify_id, 'similarity': ...}
    recommendation_data_list = get_recommendations(track.spotify_id, top_tracks + recently_played, limit=5)

    # Extract spotify_ids from the recommendation data
    recommendation_spotify_ids = [rec['id'] for rec in recommendation_data_list if isinstance(rec, dict) and 'id' in rec]

    # Optimized query for fetching recommendation Track objects
    # Ensure we only query if there are IDs to fetch
    if recommendation_spotify_ids:
        recommendations_qs = Track.objects.filter(spotify_id__in=recommendation_spotify_ids).prefetch_related('artists', 'genres')
        # To maintain the order from get_recommendations if important:
        recommendations_map = {t.spotify_id: t for t in recommendations_qs}
        recommendations = [recommendations_map[sid] for sid in recommendation_spotify_ids if sid in recommendations_map]
    else:
        recommendations = []
    artists = [
        {'name': artist.name.strip(), 'url': reverse('artist_detail', args=[artist.name.strip()])}
        for artist in track.artists.all()]

    # Direct Spotify audio feature fetching removed.
    # Celery task queuing (if features are missing) is the primary mechanism now.
    # Fallback to JioSaavn/Librosa for Celery task if still no audio_features
    if not track.audio_features:
        query = f"{track.title} {''.join([artist.name for artist in track.artists.all()])} {track.album}".strip()
        search_current_track = search_jiosaavn(query)
        if search_current_track:
            # Assuming search_jiosaavn returns a list and we take the first result
            jiosaavn_track_details = get_track_details_jiosaavn(search_current_track[0]['id'])
            if jiosaavn_track_details and jiosaavn_track_details.get('preview_url'):
                # Call the Celery task to extract features in the background
                # Pass track.id (PK) and the preview_url obtained from JioSaavn
                extract_track_features_task.delay(track.id, jiosaavn_track_details['preview_url'])
                print(f"Queued feature extraction for track {track.id} using URL: {jiosaavn_track_details['preview_url']}")
                # Optionally, update track.preview_url immediately if it's missing and JioSaavn provides one
                if not track.preview_url:
                    track.preview_url = jiosaavn_track_details['preview_url']
                    track.save(update_fields=['preview_url']) # Save only preview_url field

    # Log user activity
    UserActivity.objects.create(
        user=request.user,
        activity_type='view_track',
        description=f"Viewed track: {track.title} by {', '.join([artist.name for artist in track.artists.all()])}"
    )
    context = {
        'track': track,
        'recommendations': recommendations,
        'artists': artists
    }

    return render(request, 'music/track_detail.html', context)


@login_required
@cache_page(3600)
def artist_detail(request, artist_name):
    sp = get_spotify_client(request)
    artist_id = sp.search(artist_name, type='artist')['artists']['items'][0]['id']
    artist = sp.artist(artist_id)
    top_tracks = sp.artist_top_tracks(artist_id)['tracks'][:5]
    albums = sp.artist_albums(artist_id, album_type='album', limit=5)['items']

    context = {
        'artist': artist,
        'top_tracks': top_tracks,
        'albums': albums,
    }

    UserActivity.objects.create(
        user=request.user,
        activity_type='view_artist',
        description=f"Viewed artist: {artist['name']}"
    )
    return render(request, 'music/artist_detail.html', context)


@login_required
# @cache_page(3600) # Consider cache implications if content varies by user share
def playlist_detail(request, playlist_id):
    # The get_or_create_playlist might fetch from Spotify and create a local copy.
    # We need to ensure the local Playlist object is fetched for permission checks.
    # Assuming playlist_id is the Spotify ID.

    # Try to get the local playlist first, with optimizations
    try:
        playlist = get_object_or_404(
            Playlist.objects.select_related('user').prefetch_related(
                'tracks__artists',
                'tracks__genres',
                'shared_with' # Assuming shared_with is a ManyToManyField to User
            ),
            spotify_id=playlist_id
        )
    except Http404: # Changed from Playlist.DoesNotExist because get_object_or_404 raises Http404
        # If it doesn't exist locally, try to fetch and create it (if that's the desired behavior)
        # Note: The get_or_create_playlist function might also need optimization if it's doing many queries.
        sp = get_spotify_client(request)
        playlist_data_from_spotify = sp.playlist(playlist_id) # Fetch details from Spotify
        if not playlist_data_from_spotify:
            raise Http404("Playlist not found on Spotify.")

        # This part assumes get_or_create_playlist handles local creation based on Spotify data
        # For simplicity, let's assume if it's not local, it's an error or needs creation flow.
        # The original get_or_create_playlist might need adjustment.
        # For now, let's rely on it creating/finding the local playlist instance.
        sp = get_spotify_client(request) # re-init if needed
        playlist = get_or_create_playlist(playlist_id, request, sp) # This should return a local model instance
        if not playlist: # If still not found or created
             return redirect('dashboard') # Or some error page

    # Permission check
    is_owner = (request.user == playlist.user)
    can_view = playlist.is_public or is_owner or (request.user.is_authenticated and request.user in playlist.shared_with.all())

    if not can_view:
        messages.error(request, "You do not have permission to view this playlist.")
        return HttpResponseForbidden("You do not have permission to view this playlist.")
        # Or redirect: return redirect('some_error_page_or_dashboard')

    # Sync tracks if owner or if needed (get_or_create_playlist might already do this)
    if is_owner or not playlist.tracks.exists(): # Example condition to refresh tracks
        sp = get_spotify_client(request)
        spotify_tracks = get_playlist_tracks(sp, playlist.spotify_id) # Use playlist.spotify_id
        if spotify_tracks: # Ensure tracks were actually fetched
            playlist.tracks.set(spotify_tracks) # This expects Track model instances
            # The get_playlist_tracks should ideally return local Track instances or handle their creation/retrieval
            playlist.save() # Save if tracks were updated

    context = {
        'playlist': playlist,
        'is_owner': is_owner,
        'can_view': can_view, # Potentially useful in template
    }

    UserActivity.objects.create(
        user=request.user,
        activity_type='view_playlist',
        description=f"Viewed playlist: {playlist.name}"
    )
    return render(request, 'music/playlist_detail.html', context)


@login_required
def create_playlist(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description', '')
        cover_image = request.FILES.get('cover_image')
        if name:
            sp = get_spotify_client(request)
            playlist = create_playlist_spotify(sp, name, description)

            if cover_image:
                base64_img = convert_image_to_base64(cover_image)
                sp.playlist_upload_cover_image(playlist['id'], base64_img)
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
    return render(request, 'music/create_playlist.html')


@login_required
def add_to_playlist(request):
    if request.method == 'POST':
        track_id = request.POST.get('track_id')
        playlist_id = request.POST.get('playlist_id')
        if track_id and playlist_id:
            sp = get_spotify_client(request)
            add_tracks_to_playlist_spotify(sp, playlist_id, [track_id])
            playlist = Playlist.objects.get(spotify_id=playlist_id, user=request.user)
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
    return JsonResponse({'status': 'error'}, status=400)


@login_required
def edit_playlist_settings(request, playlist_id):
    playlist = get_object_or_404(Playlist, spotify_id=playlist_id)

    if request.user != playlist.user:
        messages.error(request, "You do not have permission to edit this playlist.")
        return HttpResponseForbidden("You cannot edit this playlist.")

    if request.method == 'POST':
        # Pass instance to the form for update, and request.FILES if handling image uploads via this form
        form = PlaylistSettingsForm(request.POST, request.FILES, instance=playlist)
        if form.is_valid():
            form.save() # This will save changes to 'name', 'description', 'is_public', 'shared_with', and 'image_url' if included

            # Optional: Sync changes to Spotify if they were made locally
            # sp = get_spotify_client(request)
            # sp.playlist_change_details(
            #     playlist.spotify_id,
            #     name=playlist.name,
            #     public=playlist.is_public,
            #     description=playlist.description if playlist.description else "" # Spotify requires description to be string
            # )
            # If image_url was changed and represents a local file to upload to Spotify:
            # if 'image_url' in form.changed_data and playlist.image_url: # Assuming image_url field is path to new local image
            #    base64_img = convert_image_to_base64(playlist.image_url.path) # If it's a FileField
            #    sp.playlist_upload_cover_image(playlist.spotify_id, base64_img)

            messages.success(request, "Playlist settings updated successfully.")
            return redirect('playlist_detail', playlist_id=playlist.spotify_id)
    else:
        # Populate form with existing playlist data
        form = PlaylistSettingsForm(instance=playlist)

    context = {
        'form': form,
        'playlist': playlist,
    }
    return render(request, 'music/edit_playlist_settings.html', context)


@login_required
def delete_playlist(request, playlist_id):
    playlist = get_object_or_404(Playlist, spotify_id=playlist_id)
    if request.user != playlist.user:
        return HttpResponseForbidden("You don't have permission to delete this playlist.")

    if request.method == 'DELETE':
        sp = get_spotify_client(request)
        delete_playlist_spotify(sp, playlist_id=playlist_id)
        playlist.delete()
        messages.success(request, 'Playlist deleted successfully.')
        return redirect('dashboard')

    return HttpResponseForbidden("Invalid request method.")


@login_required
def delete_track(request, playlist_id, track_id):
    playlist = get_object_or_404(Playlist, spotify_id=playlist_id)
    track = get_object_or_404(Track, spotify_id=track_id)

    if request.user != playlist.user:
        return HttpResponseForbidden("You don't have permission to modify this playlist.")

    if request.method == 'DELETE':
        sp = get_spotify_client(request)
        remove_tracks_from_playlist_spotify(sp, playlist_id, [track_id])
        playlist.tracks.remove(track)
        messages.success(request, 'Track removed from playlist successfully.')
        return redirect('playlist_detail', playlist_id=playlist_id)

    return HttpResponseForbidden("Invalid request method.")
