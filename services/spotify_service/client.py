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
from sklearn.metrics.pairwise import cosine_similarity
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from django.core.cache import cache
import hashlib
from django.contrib.auth import get_user_model
from django.utils import timezone

from music.models import Track, Playlist, Artist, Genre
from music.utils import translate_text

logger = logging.getLogger(__name__)

def get_spotify_client(request): # For request-based authentication
    cache_handler = spotipy.cache_handler.DjangoSessionCacheHandler(request)
    auth_manager = SpotifyOAuth(
        client_id=settings.SPOTIFY_CLIENT_ID,
        client_secret=settings.SPOTIFY_CLIENT_SECRET,
        redirect_uri=settings.SPOTIFY_REDIRECT_URI,
        scope=settings.SPOTIFY_SCOPE,
        cache_handler=cache_handler
    )
    return spotipy.Spotify(auth_manager=auth_manager)

def get_spotify_client_for_user(user_id):
    """
    Creates a Spotipy client for a given user ID using stored OAuth tokens.
    Handles token refresh if necessary.
    This is a conceptual implementation as CustomUser model changes for tokens are currently blocked by migration issues.
    """
    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.error(f"User with ID {user_id} not found for creating Spotify client.")
        return None

    # --- Conceptual: These fields need to be added to CustomUser model and migrated ---
    if not hasattr(user, 'spotify_refresh_token') or not user.spotify_refresh_token:
        logger.warning(f"User {user.username} does not have a Spotify refresh token. Model fields may be missing or not populated.")
        return None

    token_info = {
        'access_token': getattr(user, 'spotify_access_token', None),
        'refresh_token': getattr(user, 'spotify_refresh_token', None),
        'expires_at': getattr(user, 'spotify_token_expiry', None).timestamp() if getattr(user, 'spotify_token_expiry', None) else None,
        'scope': getattr(user, 'spotify_scope', settings.SPOTIFY_SCOPE) # Fallback to default scope
    }
    # --- End Conceptual ---

    if not token_info['refresh_token']: # Still check after getattr
        logger.warning(f"User {user.username} does not have a Spotify refresh token value after attempting to read from model.")
        return None

    # Ensure redirect_uri is valid; for client_credentials or refresh token flow, it might not be used by Spotipy's refresh logic
    # but SpotipyOAuth constructor requires it.
    oauth_manager = SpotifyOAuth(
        client_id=settings.SPOTIFY_CLIENT_ID,
        client_secret=settings.SPOTIFY_CLIENT_SECRET,
        redirect_uri=settings.SPOTIFY_REDIRECT_URI,
        scope=token_info['scope'],
        # No cache handler for background tasks typically, token management is manual here
    )

    if token_info.get('expires_at') and token_info['expires_at'] < timezone.now().timestamp():
        logger.info(f"Spotify token expired or missing for user {user.username}. Refreshing...")
        try:
            new_token_info = oauth_manager.refresh_access_token(token_info['refresh_token'])
            if new_token_info:
                token_info = new_token_info
                # Conceptual: Update user model with new token info
                # user.spotify_access_token = token_info['access_token']
                # user.spotify_refresh_token = token_info.get('refresh_token', user.spotify_refresh_token) # Keep old if not provided
                # user.spotify_token_expiry = timezone.make_aware(datetime.fromtimestamp(token_info['expires_at']))
                # user.spotify_scope = token_info['scope']
                # user.save(update_fields=['spotify_access_token', 'spotify_refresh_token', 'spotify_token_expiry', 'spotify_scope'])
                logger.info(f"Successfully refreshed Spotify token for user {user.username} (conceptual save)")
            else:
                logger.error(f"Refreshing Spotify token returned None for user {user.username}")
                return None
        except Exception as e:
            logger.error(f"Error refreshing Spotify token for user {user.username}: {str(e)}")
            return None

    if not token_info.get('access_token'):
         logger.error(f"No valid access token available for user {user.username} after potential refresh.")
         return None

    return spotipy.Spotify(auth=token_info['access_token'])


def search_tracks(sp, query, limit=10):
    cache_key = f"spotify_search_tracks_query_{query}_limit_{limit}"
    cached_results = cache.get(cache_key)
    if cached_results:
        logger.info(f"Returning cached Spotify search results for query: {query}")
        return cached_results
    try:
        logger.info(f"Fetching fresh Spotify search results for query: {query}")
        results = sp.search(q=query, type='track', limit=limit)
        tracks = []
        for spotify_track in results['tracks']['items']:
            track = get_or_create_track(spotify_track, sp)
            if track: tracks.append(track)
        cache.set(cache_key, tracks, timeout=900)
        return tracks
    except Exception as e:
        logger.error(f"Error searching tracks: {str(e)}"); return []

def get_or_create_track(track_data, sp: Spotify):
    spotify_id = track_data.get('id')
    if not spotify_id: return None
    try:
        track = Track.objects.get(spotify_id=spotify_id)
        # Optionally update fields like popularity if track_data has newer info
        # if track_data.get('popularity') and track.popularity != track_data.get('popularity'):
        #     track.popularity = track_data.get('popularity')
        #     track.save(update_fields=['popularity'])
        return track
    except Track.DoesNotExist:
        artists = []
        if 'artists' in track_data:
            for artist_data in track_data['artists']:
                if artist_data.get('id'):
                    artist, _ = Artist.objects.get_or_create(
                        spotify_id=artist_data['id'],
                        defaults={'name': artist_data['name']}
                    )
                    artists.append(artist)

        artist_ids = [artist.spotify_id for artist in artists if artist.spotify_id]
        genres_set = set()
        if artist_ids and sp: # Ensure sp is passed and valid for this operation
            try:
                spotify_artists_data = sp.artists(artist_ids).get('artists', [])
                for spotify_artist_item, artist_obj in zip(spotify_artists_data, artists):
                    if spotify_artist_item and spotify_artist_item.get('genres'):
                        genre_objs = []
                        for genre_name in spotify_artist_item['genres']:
                            genre_obj, _ = Genre.objects.get_or_create(name=genre_name)
                            genre_objs.append(genre_obj)
                        if genre_objs: artist_obj.genres.set(genre_objs)
                        genres_set.update(spotify_artist_item['genres'])
            except Exception as e: logger.error(f"Error fetching artist genres: {e}")

        primary_genre_name = next(iter(genres_set), 'Unknown')
        genre, _ = Genre.objects.get_or_create(name=primary_genre_name)

        release_date_val = None
        if track_data.get('album') and track_data['album'].get('release_date'):
            release_date_str = track_data['album']['release_date']
            release_precision = track_data['album'].get('release_date_precision')
            try:
                if release_precision == 'day': release_date_val = datetime.strptime(release_date_str, '%Y-%m-%d').date()
                elif release_precision == 'month': release_date_val = datetime.strptime(release_date_str, '%Y-%m').date().replace(day=1)
                elif release_precision == 'year': release_date_val = datetime.strptime(release_date_str, '%Y').date().replace(month=1, day=1)
            except ValueError: release_date_val = None

        new_track = Track(
            title=track_data.get('name'),
            spotify_id=spotify_id,
            album=track_data.get('album', {}).get('name'),
            duration=timedelta(milliseconds=track_data.get('duration_ms', 0)),
            preview_url=track_data.get('preview_url'),
            image_url=track_data.get('album', {}).get('images', [{}])[0].get('url') if track_data.get('album', {}).get('images') else None,
            popularity=track_data.get('popularity', 0),
            release_date=release_date_val,
            genres=genre, # Assign the primary genre
            audio_features={}
        )
        new_track.save() # Save first before M2M
        if artists: new_track.artists.set(artists)

        if sp: # Fetch Spotify audio features only if sp client is available
            try:
                spotify_features = sp.audio_features(tracks=[new_track.spotify_id])
                if spotify_features and spotify_features[0]:
                    sf = spotify_features[0]
                    new_track.audio_features = {
                        'source': 'spotify', 'tempo': sf.get('tempo'), 'energy': sf.get('energy'),
                        'danceability': sf.get('danceability'), 'valence': sf.get('valence'),
                        'acousticness': sf.get('acousticness'), 'instrumentalness': sf.get('instrumentalness'),
                        'liveness': sf.get('liveness'), 'speechiness': sf.get('speechiness'),
                        'loudness': sf.get('loudness'), 'mode': sf.get('mode'), 'key': sf.get('key'),
                        'time_signature': sf.get('time_signature'),
                    }
                    new_track.save(update_fields=['audio_features'])
                    logger.info(f"Fetched Spotify audio features for new track {new_track.spotify_id}")
            except Exception as e: logger.error(f"Error fetching Spotify audio features for new track {new_track.spotify_id}: {e}")
        return new_track

# ... (rest of the functions: update_song_custom_audio_features, get_or_create_playlist, etc. remain unchanged from their last correct state) ...
# ... (Make sure all previous caching logic for other functions is preserved) ...

# Preserving other functions with their caching logic from previous steps:

def update_song_custom_audio_features(song, audio_features):
    if song.audio_features is None: song.audio_features = {}
    song.audio_features.update({
        'source': 'librosa', 'tempo': audio_features.get('tempo'),
        "chroma_stft_mean": audio_features.get('chroma_stft_mean'),
        "rmse_mean": audio_features.get('rmse_mean'),
        "spectral_centroid_mean": audio_features.get('spectral_centroid_mean'),
        "spectral_bandwidth_mean": audio_features.get('spectral_bandwidth_mean'),
        "rolloff_mean": audio_features.get('rolloff_mean'),
        "zero_crossing_rate_mean": audio_features.get('zero_crossing_rate_mean'),
        "mfcc_mean": audio_features.get('mfcc_mean')
    })
    song.save()

def get_user_playlists(sp: Spotify, request):
    user_id_for_cache = request.user.id if request and hasattr(request, 'user') and request.user.is_authenticated else 'anonymous_spotify_user'
    cache_key = f"spotify_user_playlists_user_{user_id_for_cache}"
    cached_results = cache.get(cache_key)
    if cached_results: return cached_results
    logger.info(f"Fetching fresh user playlists for user: {user_id_for_cache}")
    playlists_data = sp.current_user_playlists()
    result = []
    for playlist_item in playlists_data['items']:
        pl = get_or_create_playlist(playlist_item['id'], request, sp)
        if pl: result.append(pl)
    cache.set(cache_key, result, timeout=3600)
    return result

def get_playlist_tracks(sp: Spotify, playlist_id):
    cache_key = f"spotify_playlist_tracks_id_{playlist_id}"
    cached_results = cache.get(cache_key)
    if cached_results: return cached_results
    logger.info(f"Fetching fresh playlist tracks for playlist_id: {playlist_id}")
    results = sp.playlist_items(playlist_id)
    tracks = []
    for item in results['items']:
        if item.get('track') and item['track'].get('id'):
            track = get_or_create_track(item['track'], sp)
            if track: tracks.append(track)
    cache.set(cache_key, tracks, timeout=3600)
    return tracks

def get_user_top_tracks(sp: Spotify):
    try: user_id_for_cache = sp.current_user()['id'] if sp.current_user() else 'unknown_spotify_user'
    except Exception: user_id_for_cache = 'default_spotify_user_top_tracks'
    cache_key = f"spotify_user_top_tracks_user_{user_id_for_cache}"
    cached_results = cache.get(cache_key)
    if cached_results: return cached_results
    logger.info(f"Fetching fresh user top tracks for user: {user_id_for_cache}")
    results = sp.current_user_top_tracks(limit=15, time_range='medium_term')
    top_tracks = []
    for item in results['items']:
        if item and item.get('id'): # Ensure item itself and its ID are not None
            track = get_or_create_track(item, sp)
            if track: top_tracks.append({'name': track.title, 'artist': track.artists.all().first().name if track.artists.exists() else '', 'album': track.album, 'id': track.spotify_id})
    cache.set(cache_key, top_tracks, timeout=3600)
    return top_tracks

def get_user_recently_played(sp: Spotify):
    try: user_id_for_cache = sp.current_user()['id'] if sp.current_user() else 'unknown_spotify_user_recent'
    except Exception: user_id_for_cache = 'default_spotify_user_recent_tracks'
    cache_key = f"spotify_user_recently_played_user_{user_id_for_cache}"
    cached_results = cache.get(cache_key)
    if cached_results: return cached_results
    logger.info(f"Fetching fresh user recently played for user: {user_id_for_cache}")
    results = sp.current_user_recently_played(limit=15)
    recently_played = []
    for item in results['items']:
        if item.get('track') and item['track'].get('id'):
            track = get_or_create_track(item['track'], sp)
            if track: recently_played.append({'name': track.title, 'artist': track.artists.all().first().name if track.artists.exists() else '', 'album': track.album, 'id': track.spotify_id, 'played_at': item['played_at'], 'duration': item['track']['duration_ms']})
    cache.set(cache_key, recently_played, timeout=1800)
    return recently_played

def get_artist_top_tracks(sp, artist_id):
    cache_key = f"spotify_artist_top_tracks_id_{artist_id}"
    cached_results = cache.get(cache_key)
    if cached_results: return cached_results
    logger.info(f"Fetching fresh artist top tracks for artist_id: {artist_id}")
    results = sp.artist_top_tracks(artist_id)
    tracks_data = results['tracks']
    cache.set(cache_key, tracks_data, timeout=3600)
    return tracks_data

def get_artist_details(sp, artist_id):
    cache_key = f"spotify_artist_details_id_{artist_id}"
    cached_results = cache.get(cache_key)
    if cached_results: return cached_results
    logger.info(f"Fetching fresh artist details for artist_id: {artist_id}")
    artist_data = sp.artist(artist_id)
    cache.set(cache_key, artist_data, timeout=86400)
    return artist_data

def get_artist_albums(sp, artist_id, album_type='album', limit=5):
    cache_key = f"spotify_artist_albums_id_{artist_id}_type_{album_type}_limit_{limit}"
    cached_results = cache.get(cache_key)
    if cached_results: return cached_results
    logger.info(f"Fetching fresh artist albums for artist_id: {artist_id}")
    albums_data = sp.artist_albums(artist_id, album_type=album_type, limit=limit)['items']
    cache.set(cache_key, albums_data, timeout=86400)
    return albums_data

def search_jiosaavn(query, limit=10):
    cache_key = f"jiosaavn_search_query_{query}_limit_{limit}"
    cached_results = cache.get(cache_key)
    if cached_results: return cached_results
    try:
        logger.info(f"Fetching fresh JioSaavn search for query: {query}")
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
                if song_details:
                    processed_tracks.append({
                        'id': song['id'], 'name': song['title'], 'artist': song_details['artist'],
                        'album': song_details['album'], 'year': song_details['year'],
                        'image_url': song_details['image_url'], 'duration': song_details['duration'],
                        'preview_url': song_details['preview_url'],
                    })
            except Exception as e: logger.error(f"Error processing JioSaavn track {song.get('id')}: {str(e)}")
        cache.set(cache_key, processed_tracks, timeout=3600)
        return processed_tracks
    except Exception as e: logger.error(f"Error searching JioSaavn: {str(e)}"); return []

def get_track_details_jiosaavn(track_id):
    cache_key = f"jiosaavn_track_details_id_{track_id}"
    cached_results = cache.get(cache_key)
    if cached_results: return cached_results
    try:
        logger.info(f"Fetching fresh JioSaavn track details for id: {track_id}")
        url = f"https://www.jiosaavn.com/api.php?__call=song.getDetails&cc=in&_marker=0%3F_marker%3D0&_format=json&pids={track_id}"
        response = requests.get(url, timeout=10)
        if response.status_code != 200: return None
        data = response.json()
        if not data or track_id not in data: return None
        song_data = data[track_id]
        standardized_data = {
            'id': song_data['id'], 'name': song_data['song'], 'artist': song_data['primary_artists'],
            'album': song_data['album'], 'year': song_data['year'],
            'image_url': re.sub(r'\d+x\d+', '500x500', song_data['image']),
            'duration': int(song_data['duration']) * 1000,
            'preview_url': song_data.get('vlink') or song_data.get('media_preview_url', '')
        }
        cache.set(cache_key, standardized_data, timeout=86400)
        return standardized_data
    except Exception as e: logger.error(f"Error getting JioSaavn track details: {str(e)}"); return None

def extract_audio_features(audio_file):
    cache_key = f"librosa_extract_audio_features_path_{audio_file}"
    try:
        file_mod_time = os.path.getmtime(audio_file)
        cache_key += f"_mod_{file_mod_time}"
    except OSError: pass
    cached_results = cache.get(cache_key)
    if cached_results: return cached_results
    try:
        logger.info(f"Extracting fresh Librosa features for file: {audio_file}")
        y, sr = librosa.load(audio_file, duration=30, res_type='kaiser_fast')
    except Exception as e: logger.error(f"Error loading audio file: {e}"); return None
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    features = {
        'tempo': float(tempo), 'chroma_stft_mean': float(np.mean(librosa.feature.chroma_stft(y=y, sr=sr))),
        'rmse_mean': float(np.mean(librosa.feature.rms(y=y))),
        'spectral_centroid_mean': float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr))),
        'spectral_bandwidth_mean': float(np.mean(librosa.feature.spectral_bandwidth(y=y, sr=sr))),
        'rolloff_mean': float(np.mean(librosa.feature.spectral_rolloff(y=y, sr=sr))),
        'zero_crossing_rate_mean': float(np.mean(librosa.feature.zero_crossing_rate(y))),
        'mfcc_mean': float(np.mean(librosa.feature.mfcc(y=y, sr=sr))),
    }
    cache.set(cache_key, features, timeout=None)
    return features

def download_preview(preview_url, track_id):
    if not preview_url or not preview_url.startswith("http"): return None
    file_path = os.path.join(settings.MEDIA_ROOT, 'previews', f'{track_id}.mp3')
    if os.path.exists(file_path): return file_path
    try:
        response = requests.get(preview_url, stream=True, timeout=10)
        response.raise_for_status()
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192): f.write(chunk)
        return file_path
    except (requests.RequestException, IOError) as e:
        logger.error(f"Error downloading preview for {track_id}: {str(e)}"); return None

STANDARD_SIMILARITY_FEATURES = [
    'acousticness', 'danceability', 'energy', 'instrumentalness',
    'liveness', 'loudness', 'speechiness', 'tempo', 'valence', 'mode', 'key'
]
def _normalize_loudness(loudness_db):
    if loudness_db is None: return 0.5
    return max(0.0, min(1.0, (loudness_db + 60) / 60))
def _normalize_tempo(tempo_bpm):
    if tempo_bpm is None: return 0.5
    return max(0.0, min(1.0, (tempo_bpm - 50) / 200))

def _get_standardized_features_for_track(track_obj, sp_client):
    features_to_standardize = None
    source_of_features = None
    if track_obj.audio_features and isinstance(track_obj.audio_features, dict) and 'source' in track_obj.audio_features:
        features_to_standardize = track_obj.audio_features
        source_of_features = track_obj.audio_features['source']
    if not features_to_standardize or source_of_features not in ['spotify', 'librosa']:
        if track_obj.spotify_id and sp_client:
            try:
                spotify_api_features_list = sp_client.audio_features(tracks=[track_obj.spotify_id])
                if spotify_api_features_list and spotify_api_features_list[0]:
                    sf = spotify_api_features_list[0]
                    track_obj.audio_features = {
                        'source': 'spotify', 'tempo': sf.get('tempo'), 'energy': sf.get('energy'),
                        'danceability': sf.get('danceability'), 'valence': sf.get('valence'),
                        'acousticness': sf.get('acousticness'), 'instrumentalness': sf.get('instrumentalness'),
                        'liveness': sf.get('liveness'), 'speechiness': sf.get('speechiness'),
                        'loudness': sf.get('loudness'), 'mode': sf.get('mode'), 'key': sf.get('key'),
                        'time_signature': sf.get('time_signature'),
                    }
                    track_obj.save()
                    features_to_standardize = track_obj.audio_features
                    source_of_features = 'spotify'
            except Exception as e:
                track_obj.audio_features = {'source': 'spotify_error', 'error': str(e)}; track_obj.save()
    if not features_to_standardize or source_of_features not in ['spotify', 'librosa']:
        preview_url_to_use = track_obj.preview_url
        if not preview_url_to_use:
            try:
                artist_name_for_search = track_obj.artists.first().name if track_obj.artists.exists() else ""
                search_query = f"{track_obj.title} {artist_name_for_search}".strip()
                jiosaavn_results = search_jiosaavn(search_query, limit=1)
                if jiosaavn_results and jiosaavn_results[0].get('preview_url'):
                    preview_url_to_use = jiosaavn_results[0]['preview_url']
            except Exception: pass
        if preview_url_to_use:
            preview_file_path = download_preview(preview_url_to_use, track_obj.spotify_id)
            if preview_file_path:
                librosa_features = extract_audio_features(preview_file_path)
                if librosa_features:
                    update_song_custom_audio_features(track_obj, librosa_features)
                    features_to_standardize = track_obj.audio_features
                    source_of_features = 'librosa'
    if not features_to_standardize or source_of_features not in ['spotify', 'librosa']: return None
    feature_vector = []
    if source_of_features == 'spotify':
        for key in STANDARD_SIMILARITY_FEATURES:
            val = features_to_standardize.get(key)
            if key == 'loudness': val = _normalize_loudness(val)
            elif key == 'tempo': val = _normalize_tempo(val)
            elif key == 'key': val = float(val / 11) if val is not None else 0.5
            elif key == 'mode': val = float(val) if val is not None else 0.5
            feature_vector.append(float(val) if val is not None else 0.5)
    elif source_of_features == 'librosa':
        feature_vector.extend([
            _normalize_tempo(features_to_standardize.get('tempo')),
            features_to_standardize.get('rmse_mean', 0.5), 0.5, 0.5,
            features_to_standardize.get('zero_crossing_rate_mean', 0.5), 0.5, 0.5,
            features_to_standardize.get('spectral_bandwidth_mean', 0.5),
            features_to_standardize.get('rmse_mean', 0.5), 0.5, 0.5
        ])
        feature_vector = (feature_vector + [0.5] * len(STANDARD_SIMILARITY_FEATURES))[:len(STANDARD_SIMILARITY_FEATURES)]
    else: return None
    return feature_vector

# @tiered_cache('recommendations', timeout=3600) # Decorator removed
def get_recommendations(track_id, stored_tracks_data, limit=10, sp_client=None):
    cache_key = f"spotify_recommendations_track_{track_id}_limit_{limit}_stored_tracks_hash_{hash(str(stored_tracks_data))}"
    cached_results = cache.get(cache_key)
    if cached_results: return cached_results
    logger.info(f"Generating fresh recommendations for track_id: {track_id}")
    try:
        target_track_obj = Track.objects.select_related('genres').prefetch_related('artists').get(spotify_id=track_id)
    except Track.DoesNotExist: return []
    target_feature_vector = _get_standardized_features_for_track(target_track_obj, sp_client)
    if not target_feature_vector: return []
    stored_track_ids = [st['id'] for st in stored_tracks_data if st['id'] != target_track_obj.spotify_id]
    db_stored_tracks = Track.objects.filter(spotify_id__in=stored_track_ids).select_related('genres').prefetch_related('artists')
    db_stored_tracks_map = {t.spotify_id: t for t in db_stored_tracks}
    similarities = []
    for input_track_data in stored_tracks_data:
        current_track_id = input_track_data['id']
        if current_track_id == target_track_obj.spotify_id: continue
        stored_track_obj = db_stored_tracks_map.get(current_track_id)
        if not stored_track_obj: continue
        stored_feature_vector = _get_standardized_features_for_track(stored_track_obj, sp_client)
        if not stored_feature_vector: continue
        target_np = np.array(target_feature_vector).reshape(1, -1)
        stored_np = np.array(stored_feature_vector).reshape(1, -1)
        similarity = cosine_similarity(target_np, stored_np)[0][0]
        similarities.append({'id': current_track_id, 'similarity': similarity})
    recommendations = sorted(similarities, key=lambda x: x['similarity'], reverse=True)[:limit]
    cache.set(cache_key, recommendations, timeout=3600)
    return recommendations

def batch_extract_audio_features(audio_files):
    results = {}
    with ThreadPoolExecutor(max_workers=min(8, len(audio_files))) as executor:
        future_to_file = {executor.submit(extract_audio_features, file_path): track_id for track_id, file_path in audio_files}
        for future in concurrent.futures.as_completed(future_to_file):
            track_id = future_to_file[future]
            try:
                features = future.result()
                if features: results[track_id] = features
            except Exception as e: logger.error(f"Error extracting features for {track_id}: {str(e)}")
    return results

def create_playlist_spotify(sp: Spotify, name, description=""):
    user_id = sp.current_user()['id']
    return sp.user_playlist_create(user_id, name, public=False, description=description)

def add_tracks_to_playlist_spotify(sp: Spotify, playlist_id, track_ids):
    track_ids = [f"spotify:track:{track_id}" for track_id in track_ids]
    sp.playlist_add_items(playlist_id, track_ids)

def remove_tracks_from_playlist_spotify(sp: Spotify, playlist_id, track_ids):
    track_ids = [f"spotify:track:{track_id}" for track_id in track_ids]
    sp.playlist_remove_all_occurrences_of_items(playlist_id, track_ids)

def delete_playlist_spotify(sp: Spotify, playlist_id):
    sp.current_user_unfollow_playlist(playlist_id)

# @tiered_cache('listening_time', timeout=3600) # Removed tiered_cache
def calculate_listening_time(sp: Spotify, recently_played):
    if not recently_played: return 0.0
    try:
        if not isinstance(recently_played, list) or \
           not all(isinstance(item, dict) and ('id' in item or 'duration' in item) for item in recently_played):
            logger.warning("calculate_listening_time: recently_played structure not as expected, skipping cache.")
            total_duration_ms = sum(track.get('duration', 0) for track in recently_played if isinstance(track, dict))
            return total_duration_ms / (1000 * 60 * 60)
        rp_identifier_str = "_".join(sorted([f"{t.get('id', 'no_id')}_{t.get('duration', 0)}" for t in recently_played]))
        rp_hash = hashlib.md5(rp_identifier_str.encode('utf-8')).hexdigest()
    except TypeError as e:
        logger.error(f"Error creating identifier for recently_played in calculate_listening_time: {e}. Skipping cache.")
        total_duration_ms = sum(track.get('duration', 0) for track in recently_played if isinstance(track, dict))
        return total_duration_ms / (1000 * 60 * 60)
    cache_key = f"spotify_listening_time_hash_{rp_hash}"
    cached_result = cache.get(cache_key)
    if cached_result is not None: return cached_result
    logger.info(f"Calculating fresh listening time for hash: {rp_hash}")
    total_duration_ms = sum(track.get('duration', 0) for track in recently_played if isinstance(track, dict))
    total_hours = total_duration_ms / (1000 * 60 * 60)
    cache.set(cache_key, total_hours, timeout=3600)
    return total_hours

# @tiered_cache('favorite_genre', timeout=3600) # Removed tiered_cache
def get_favorite_genre(sp: Spotify, top_tracks):
    top_tracks_identifier = hashlib.md5(str(sorted(top_tracks, key=lambda x: x['id'])).encode('utf-8')).hexdigest() if top_tracks else "no_tracks"
    try:
        user_id_for_cache = sp.current_user()['id'] if sp.current_user() else 'unknown_user_fav_genre'
    except Exception: user_id_for_cache = 'default_user_fav_genre'
    cache_key = f"spotify_favorite_genre_user_{user_id_for_cache}_tracks_{top_tracks_identifier}"
    cached_results = cache.get(cache_key)
    if cached_results: return cached_results
    logger.info(f"Calculating fresh favorite genre for user: {user_id_for_cache}")
    if not top_tracks: return None
    artist_names = [track['artist'] for track in top_tracks]
    all_genres = []
    missing_artists = []
    artist_query = Q()
    for name in artist_names: artist_query |= Q(name__iexact=name)
    existing_artists = Artist.objects.filter(artist_query).prefetch_related('genres')
    artist_genres_map = {artist.name.lower(): list(artist.genres.values_list('name', flat=True)) for artist in existing_artists}
    for artist_name in artist_names:
        if artist_name.lower() in artist_genres_map: all_genres.extend(artist_genres_map[artist_name.lower()])
        else: missing_artists.append(artist_name)
    if missing_artists:
        try:
            def fetch_artist_genres(artist_name_inner): # Renamed to avoid conflict
                try:
                    s_results = sp.search(artist_name_inner, type='artist', limit=1)
                    if not s_results['artists']['items']: return []
                    s_artist_data = s_results['artists']['items'][0]
                    s_genres = s_artist_data.get('genres', [])
                    art_obj, created = Artist.objects.get_or_create(spotify_id=s_artist_data['id'], defaults={'name': s_artist_data['name']})
                    if created or not art_obj.genres.exists():
                        g_objs = [Genre.objects.get_or_create(name=g_name)[0] for g_name in s_genres]
                        art_obj.genres.set(g_objs)
                    return s_genres
                except Exception as e_inner: logger.error(f"Error fetching genres for {artist_name_inner}: {str(e_inner)}"); return []
            with ThreadPoolExecutor(max_workers=min(10, len(missing_artists))) as executor:
                future_to_artist = {executor.submit(fetch_artist_genres, artist_name_item): artist_name_item for artist_name_item in missing_artists}
                for future in concurrent.futures.as_completed(future_to_artist):
                    genres_list = future.result()
                    all_genres.extend(genres_list)
        except Exception as e_outer: logger.error(f"Error fetching artist genres from Spotify: {e_outer}")
    if not all_genres: cache.set(cache_key, None, timeout=3600); return None
    most_common_genre = Counter(all_genres).most_common(1)[0][0]
    cache.set(cache_key, most_common_genre, timeout=3600)
    return most_common_genre

# Standardize ThreadPoolExecutor usage for functions that were using it.
# Note: The ThreadPoolExecutor was removed from get_user_playlists and get_playlist_tracks for simplicity
# in the previous caching refactor. If performance for those is critical and they involve many sub-tasks,
# it can be re-added. For now, the focus is on correct caching and structure.
# The jiosaavn search and get_favorite_genre still use it.
# Batch_extract_audio_features also uses it.
