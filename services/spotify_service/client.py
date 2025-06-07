import concurrent
import os
import re
from collections import Counter
from datetime import datetime, timedelta
import logging

import librosa
import numpy as np
import requests
import spotipy
from django.conf import settings
from django.db.models import Q
# from sklearn.metrics.pairwise import cosine_similarity # No longer needed here
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from django.core.cache import cache
import hashlib
from django.contrib.auth import get_user_model
from django.utils import timezone

from music.models import Track, Playlist, Artist, Genre
from music.utils import translate_text
from services.utils import cache_api_call

logger = logging.getLogger(__name__)

def get_spotify_client(request): # For request-based authentication (uses session cache via Spotipy)
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

def get_spotify_client_for_user(user_id):
    User = get_user_model()
    try: user = User.objects.get(id=user_id)
    except User.DoesNotExist: logger.error(f"User {user_id} not found for Spotify client."); return None

    spotify_access_token = getattr(user, 'spotify_access_token', None)
    spotify_refresh_token = getattr(user, 'spotify_refresh_token', None)
    spotify_token_expiry = getattr(user, 'spotify_token_expiry', None)
    spotify_scope = getattr(user, 'spotify_scope', settings.SPOTIFY_SCOPE)

    if not spotify_refresh_token: logger.warning(f"User {user.username} no Spotify refresh token."); return None

    sp_oauth = SpotifyOAuth(client_id=settings.SPOTIFY_CLIENT_ID, client_secret=settings.SPOTIFY_CLIENT_SECRET, redirect_uri=settings.SPOTIFY_REDIRECT_URI, scope=spotify_scope, cache_handler=None)
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
                if new_refresh_token_val: user.spotify_refresh_token = new_refresh_token_val
                new_expires_at_ts = new_token_info.get('expires_at')
                if new_expires_at_ts: user.spotify_token_expiry = timezone.make_aware(datetime.fromtimestamp(new_expires_at_ts))
                user.spotify_scope = new_token_info.get('scope', spotify_scope)
                update_fields_list = ['spotify_access_token', 'spotify_refresh_token', 'spotify_token_expiry', 'spotify_scope']
                user.save(update_fields=[f for f in update_fields_list if hasattr(user, f)])
                logger.info(f"Successfully refreshed and saved Spotify token for user {user.username}.")
            else: logger.error(f"Refreshing token returned None for {user.username}."); return None
        except Exception as e: logger.error(f"Error refreshing token for {user.username}: {e}"); return None

    if not current_access_token: logger.error(f"No valid access token for {user.username}."); return None
    return spotipy.Spotify(auth=current_access_token)

@cache_api_call(key_prefix="spotify_search_tracks", timeout=900)
def search_tracks(sp, query, limit=10, user_id_for_cache=None):
    try:
        results = sp.search(q=query, type='track', limit=limit)
        tracks = []
        for spotify_track in results['tracks']['items']:
            track = get_or_create_track(spotify_track, sp)
            if track: tracks.append(track)
        return tracks
    except Exception as e: logger.error(f"Error searching Spotify tracks: {e}"); return []

def get_or_create_track(track_data, sp: Spotify):
    spotify_id = track_data.get('id')
    if not spotify_id: return None
    try: track = Track.objects.get(spotify_id=spotify_id); return track
    except Track.DoesNotExist:
        artists = []
        if 'artists' in track_data:
            for artist_data in track_data['artists']:
                if artist_data.get('id'):
                    artist, _ = Artist.objects.get_or_create(spotify_id=artist_data['id'], defaults={'name': artist_data['name']})
                    artists.append(artist)
        artist_ids = [artist.spotify_id for artist in artists if artist.spotify_id]
        genres_set = set()
        if artist_ids and sp:
            try:
                spotify_artists_data = sp.artists(artist_ids).get('artists', [])
                for spotify_artist_item, artist_obj in zip(spotify_artists_data, artists):
                    if spotify_artist_item and spotify_artist_item.get('genres'):
                        genre_objs = [Genre.objects.get_or_create(name=name)[0] for name in spotify_artist_item['genres']]
                        if genre_objs: artist_obj.genres.set(genre_objs)
                        genres_set.update(spotify_artist_item['genres'])
            except Exception as e: logger.error(f"Error fetching artist genres in get_or_create_track: {e}")
        primary_genre_name = next(iter(genres_set), 'Unknown'); genre, _ = Genre.objects.get_or_create(name=primary_genre_name)
        release_date_val = None
        if track_data.get('album') and track_data['album'].get('release_date'):
            release_date_str = track_data['album']['release_date']; release_precision = track_data['album'].get('release_date_precision')
            try:
                if release_precision == 'day': release_date_val = datetime.strptime(release_date_str, '%Y-%m-%d').date()
                elif release_precision == 'month': release_date_val = datetime.strptime(release_date_str, '%Y-%m').date().replace(day=1)
                elif release_precision == 'year': release_date_val = datetime.strptime(release_date_str, '%Y').date().replace(month=1, day=1)
            except ValueError: release_date_val = None
        new_track = Track(title=track_data.get('name'), spotify_id=spotify_id, album=track_data.get('album', {}).get('name'), duration=timedelta(milliseconds=track_data.get('duration_ms', 0)), preview_url=track_data.get('preview_url'), image_url=track_data.get('album', {}).get('images', [{}])[0].get('url') if track_data.get('album', {}).get('images') else None, popularity=track_data.get('popularity', 0), release_date=release_date_val, genres=genre, audio_features={})
        new_track.save();
        if artists: new_track.artists.set(artists)
        if sp:
            try:
                sf_list = sp.audio_features(tracks=[new_track.spotify_id])
                if sf_list and sf_list[0]:
                    sf = sf_list[0]
                    new_track.audio_features = {'source': 'spotify', 'tempo': sf.get('tempo'), 'energy': sf.get('energy'), 'danceability': sf.get('danceability'), 'valence': sf.get('valence'), 'acousticness': sf.get('acousticness'), 'instrumentalness': sf.get('instrumentalness'), 'liveness': sf.get('liveness'), 'speechiness': sf.get('speechiness'), 'loudness': sf.get('loudness'), 'mode': sf.get('mode'), 'key': sf.get('key'), 'time_signature': sf.get('time_signature')}
                    new_track.save(update_fields=['audio_features'])
            except Exception as e: logger.error(f"Error fetching Spotify audio features for new track {new_track.spotify_id}: {e}")
        return new_track

def update_song_custom_audio_features(song, audio_features):
    if song.audio_features is None: song.audio_features = {}
    song.audio_features.update({'source': 'librosa', **audio_features}); song.save()

def get_or_create_playlist(playlist_id, request_or_user, sp: Spotify):
    user_obj = request_or_user.user if hasattr(request_or_user, 'user') and request_or_user.user.is_authenticated else (request_or_user if isinstance(request_or_user, get_user_model()) else None)
    if not user_obj: logger.error("get_or_create_playlist: invalid user/request."); return None
    playlist = Playlist.objects.filter(spotify_id=playlist_id, user=user_obj).first()
    if not playlist: playlist_data = sp.playlist(playlist_id); playlist = Playlist.objects.create(user=user_obj, name=playlist_data['name'], spotify_id=playlist_data['id'], description=playlist_data['description'], image_url=playlist_data['images'][0]['url'] if playlist_data['images'] else None)
    return playlist

@cache_api_call(key_prefix="spotify_user_playlists", timeout=3600)
def get_user_playlists(sp: Spotify, request_or_user_id):
    user_id = request_or_user_id.user.id if hasattr(request_or_user_id, 'user') and hasattr(request_or_user_id.user, 'id') else request_or_user_id
    logger.info(f"Fetching fresh user playlists for user ID: {user_id}")
    playlists_data = sp.current_user_playlists()
    result = []
    user_obj_for_playlist = None
    if isinstance(user_id, int):
        try: user_obj_for_playlist = get_user_model().objects.get(id=user_id)
        except get_user_model().DoesNotExist: logger.error(f"User not found for ID {user_id} in get_user_playlists"); return []
    elif hasattr(request_or_user_id, 'user'): user_obj_for_playlist = request_or_user_id.user
    if not user_obj_for_playlist or not user_obj_for_playlist.is_authenticated: logger.warning(f"Invalid user object or unauthenticated user for get_user_playlists (user_id: {user_id})"); return []
    for playlist_item in playlists_data['items']:
        pl = get_or_create_playlist(playlist_item['id'], user_obj_for_playlist, sp)
        if pl: result.append(pl)
    return result

@cache_api_call(key_prefix="spotify_playlist_tracks", timeout=3600)
def get_playlist_tracks(sp: Spotify, playlist_id):
    results = sp.playlist_items(playlist_id); tracks = []
    for item in results['items']:
        if item.get('track') and item['track'].get('id'):
            track = get_or_create_track(item['track'], sp)
            if track: tracks.append(track)
    return tracks

@cache_api_call(key_prefix="spotify_user_top_tracks", timeout=3600)
def get_user_top_tracks(sp: Spotify, user_id_for_cache=None):
    if not user_id_for_cache:
        try: user_id_for_cache = sp.current_user()['id'] if sp.current_user() else 'unknown_spotify_user'
        except: user_id_for_cache = 'default_spotify_user_top_tracks'
    results = sp.current_user_top_tracks(limit=15, time_range='medium_term'); top_tracks = []
    for item in results['items']:
        if item and item.get('id'):
            track = get_or_create_track(item, sp)
            if track: top_tracks.append({'name': track.title, 'artist': track.artists.all().first().name if track.artists.exists() else '', 'album': track.album, 'id': track.spotify_id})
    return top_tracks

@cache_api_call(key_prefix="spotify_user_recently_played", timeout=1800)
def get_user_recently_played(sp: Spotify, user_id_for_cache=None):
    if not user_id_for_cache:
        try: user_id_for_cache = sp.current_user()['id'] if sp.current_user() else 'unknown_spotify_user_recent'
        except: user_id_for_cache = 'default_spotify_user_recent_tracks'
    results = sp.current_user_recently_played(limit=15); recently_played = []
    for item in results['items']:
        if item.get('track') and item['track'].get('id'):
            track = get_or_create_track(item['track'], sp)
            if track: recently_played.append({'name': track.title, 'artist': track.artists.all().first().name if track.artists.exists() else '', 'album': track.album, 'id': track.spotify_id, 'played_at': item['played_at'], 'duration': item['track']['duration_ms']})
    return recently_played

@cache_api_call(key_prefix="spotify_artist_top_tracks", timeout=3600)
def get_artist_top_tracks(sp, artist_id):
    return sp.artist_top_tracks(artist_id)['tracks']

@cache_api_call(key_prefix="spotify_artist_details", timeout=86400)
def get_artist_details(sp, artist_id):
    return sp.artist(artist_id)

@cache_api_call(key_prefix="spotify_artist_albums", timeout=86400)
def get_artist_albums(sp, artist_id, album_type='album', limit=5):
    return sp.artist_albums(artist_id, album_type=album_type, limit=limit)['items']

@cache_api_call(key_prefix="jiosaavn_search", timeout=3600)
def search_jiosaavn(query, limit=10):
    try:
        url = f"https://www.jiosaavn.com/api.php?__call=autocomplete.get&_format=json&_marker=0&cc=in&includeMetaTags=1&query={query}"
        response = requests.get(url, timeout=10)
        if response.status_code != 200: return []
        data = response.json()
        if not data or 'songs' not in data or 'data' not in data.get('songs', {}): return []
        processed_tracks = []
        songs_to_process = data['songs']['data'][:limit]
        for song in songs_to_process:
            try:
                song_details = get_track_details_jiosaavn(song['id'])
                if song_details: processed_tracks.append({'id': song['id'], 'name': song['title'], 'artist': song_details['artist'], 'album': song_details['album'], 'year': song_details['year'], 'image_url': song_details['image_url'], 'duration': song_details['duration'], 'preview_url': song_details['preview_url']})
            except Exception as e: logger.error(f"Error processing JioSaavn track {song.get('id')}: {str(e)}")
        return processed_tracks
    except Exception as e: logger.error(f"Error searching JioSaavn: {str(e)}"); return []

@cache_api_call(key_prefix="jiosaavn_track_details", timeout=86400)
def get_track_details_jiosaavn(track_id):
    try:
        url = f"https://www.jiosaavn.com/api.php?__call=song.getDetails&cc=in&_marker=0%3F_marker%3D0&_format=json&pids={track_id}"
        response = requests.get(url, timeout=10)
        if response.status_code != 200: return None
        data = response.json()
        if not data or track_id not in data: return None
        song_data = data[track_id]
        return {'id': song_data['id'], 'name': song_data['song'], 'artist': song_data['primary_artists'], 'album': song_data['album'], 'year': song_data['year'], 'image_url': re.sub(r'\d+x\d+', '500x500', song_data['image']), 'duration': int(song_data['duration']) * 1000, 'preview_url': song_data.get('vlink') or song_data.get('media_preview_url', '')}
    except Exception as e: logger.error(f"Error getting JioSaavn track details: {str(e)}"); return None

@cache_api_call(key_prefix="librosa_features", timeout=None)
def extract_audio_features(audio_file, file_mod_time_for_key=None):
    try: y, sr = librosa.load(audio_file, duration=30, res_type='kaiser_fast')
    except Exception as e: logger.error(f"Error loading audio file {audio_file}: {e}"); return None
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    return {'tempo': float(tempo), 'chroma_stft_mean': float(np.mean(librosa.feature.chroma_stft(y=y, sr=sr))), 'rmse_mean': float(np.mean(librosa.feature.rms(y=y))), 'spectral_centroid_mean': float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr))), 'spectral_bandwidth_mean': float(np.mean(librosa.feature.spectral_bandwidth(y=y, sr=sr))), 'rolloff_mean': float(np.mean(librosa.feature.spectral_rolloff(y=y, sr=sr))), 'zero_crossing_rate_mean': float(np.mean(librosa.feature.zero_crossing_rate(y))), 'mfcc_mean': float(np.mean(librosa.feature.mfcc(y=y, sr=sr)))}

def download_preview(preview_url, track_id):
    if not preview_url or not preview_url.startswith("http"): return None
    file_path = os.path.join(settings.MEDIA_ROOT, 'previews', f'{track_id}.mp3')
    if os.path.exists(file_path): return file_path
    try:
        response = requests.get(preview_url, stream=True, timeout=10); response.raise_for_status()
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192): f.write(chunk)
        return file_path
    except (requests.RequestException, IOError) as e: logger.error(f"Error downloading preview for {track_id}: {str(e)}"); return None

def batch_extract_audio_features(audio_files):
    results = {}
    with ThreadPoolExecutor(max_workers=min(8, len(audio_files))) as executor:
        future_to_file = {executor.submit(extract_audio_features, file_path, file_mod_time_for_key=os.path.getmtime(file_path) if os.path.exists(file_path) else None): track_id for track_id, file_path in audio_files}
        for future in concurrent.futures.as_completed(future_to_file):
            track_id = future_to_file[future]
            try:
                features = future.result()
                if features: results[track_id] = features
            except Exception as e:
                logger.error(f"Error processing future result for track_id {track_id} in batch_extract_audio_features: {str(e)}")
    return results

def create_playlist_spotify(sp: Spotify, name, description=""):
    user_id = sp.current_user()['id']
    return sp.user_playlist_create(user_id, name, public=False, description=description)

def add_tracks_to_playlist_spotify(sp: Spotify, playlist_id, track_ids):
    track_ids_formatted = [f"spotify:track:{track_id}" for track_id in track_ids]
    sp.playlist_add_items(playlist_id, track_ids_formatted)

def remove_tracks_from_playlist_spotify(sp: Spotify, playlist_id, track_ids):
    track_ids_formatted = [f"spotify:track:{track_id}" for track_id in track_ids]
    sp.playlist_remove_all_occurrences_of_items(playlist_id, track_ids_formatted)

def delete_playlist_spotify(sp: Spotify, playlist_id):
    sp.current_user_unfollow_playlist(playlist_id)

@cache_api_call(key_prefix="spotify_listening_time", timeout=3600)
def calculate_listening_time(sp: Spotify, recently_played, user_id_for_cache=None):
    if not recently_played: return 0.0
    total_duration_ms = sum(track.get('duration', 0) for track in recently_played if isinstance(track, dict))
    return total_duration_ms / (1000 * 60 * 60)

@cache_api_call(key_prefix="spotify_favorite_genre", timeout=3600)
def get_favorite_genre(sp: Spotify, top_tracks, user_id_for_cache=None):
    if not top_tracks: return None
    artist_names = [track['artist'] for track in top_tracks]
    all_genres = []; missing_artists = []
    artist_query = Q()
    for name in artist_names: artist_query |= Q(name__iexact=name)
    existing_artists = Artist.objects.filter(artist_query).prefetch_related('genres')
    artist_genres_map = {artist.name.lower(): list(artist.genres.values_list('name', flat=True)) for artist in existing_artists}
    for artist_name in artist_names:
        if artist_name.lower() in artist_genres_map: all_genres.extend(artist_genres_map[artist_name.lower()])
        else: missing_artists.append(artist_name)
    if missing_artists:
        try:
            def fetch_artist_genres(artist_name_inner):
                try:
                    s_results = sp.search(artist_name_inner, type='artist', limit=1)
                    if not s_results['artists']['items']: return []
                    s_artist_data = s_results['artists']['items'][0]; s_genres = s_artist_data.get('genres', [])
                    art_obj, created = Artist.objects.get_or_create(spotify_id=s_artist_data['id'], defaults={'name': s_artist_data['name']})
                    if created or not art_obj.genres.exists():
                        g_objs = [Genre.objects.get_or_create(name=g_name)[0] for g_name in s_genres]
                        if g_objs: art_obj.genres.set(g_objs)
                    return s_genres
                except Exception as e_inner: logger.error(f"Error fetching genres for {artist_name_inner}: {str(e_inner)}"); return []
            with ThreadPoolExecutor(max_workers=min(10, len(missing_artists))) as executor:
                future_to_artist = {executor.submit(fetch_artist_genres, name_item): name_item for name_item in missing_artists}
                for future in concurrent.futures.as_completed(future_to_artist):
                    genres_list = future.result()
                    if genres_list: all_genres.extend(genres_list)
        except Exception as e_outer: logger.error(f"Error fetching artist genres from Spotify: {e_outer}")
    if not all_genres: return None
    return Counter(all_genres).most_common(1)[0][0]

# Removed _get_standardized_features_for_track, STANDARD_SIMILARITY_FEATURES,
# _normalize_loudness, _normalize_tempo, and get_recommendations
# These are now intended to be in services/recommendation_service/
