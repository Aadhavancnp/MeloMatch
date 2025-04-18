from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.cache import cache_page

from users.models import UserActivity
from .models import Playlist, Track
from .spotify import get_recommendations, get_spotify_client, \
    search_jiosaavn, get_track_details_jiosaavn, get_user_top_tracks, get_user_recently_played, create_playlist_spotify, \
    search_tracks, get_or_create_playlist, get_playlist_tracks, extract_audio_features, download_preview, \
    add_tracks_to_playlist_spotify, delete_playlist_spotify, remove_tracks_from_playlist_spotify
from .utils import convert_image_to_base64


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
    track = Track.objects.get(spotify_id=track_id)
    sp = get_spotify_client(request)
    top_tracks = get_user_top_tracks(sp)
    recently_played = get_user_recently_played(sp)
    recommendation_ids = get_recommendations(track_id, top_tracks + recently_played, limit=5)
    recommendation_ids = list({track['id']: track for track in recommendation_ids}.values())
    recommendations = [Track.objects.get(spotify_id=track['id']) for track in recommendation_ids]
    artists = [
        {'name': artist.name.strip(), 'url': reverse('artist_detail', args=[artist.name.strip()])}
        for artist in track.artists.all()]

    query = f"{track.title} {"".join([artist.name for artist in track.artists.all()])} {track.album}".strip()
    search_current_track = search_jiosaavn(query)
    if search_current_track:
        track_details = get_track_details_jiosaavn(search_current_track[0]['id'])
        audio_features = extract_audio_features(download_preview(track_details['preview_url'], track.spotify_id))
        track.audio_features = audio_features
        track.preview_url = track_details['preview_url']
        track.save()

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
# @cache_page(3600)
def playlist_detail(request, playlist_id):
    sp = get_spotify_client(request)

    playlist = get_or_create_playlist(playlist_id, request, sp)
    if playlist:
        # The tracks_data is now handled in get_playlist_tracks and get_or_create_playlist
        spotify_tracks = get_playlist_tracks(sp, playlist_id)
        playlist.tracks.set(spotify_tracks)
        playlist.save()

    if not playlist:
        return redirect('dashboard')

    context = {
        'playlist': playlist
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
