import concurrent
import os
import re
from collections import Counter
from datetime import datetime, timedelta

import librosa
import numpy as np
import requests
import spotipy
from django.conf import settings
from django.db.models import Q
from sklearn.metrics.pairwise import cosine_similarity
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth

from core.cache import tiered_cache
from music.models import Track, Playlist, Artist, Genre

# Add parallel processing for batch extraction
from concurrent.futures import ThreadPoolExecutor

from music.utils import translate_text


@tiered_cache(maxsize=100)
def get_spotify_client(request):
    cache_handler = spotipy.cache_handler.DjangoSessionCacheHandler(request)
    auth_manager = SpotifyOAuth(
        client_id=settings.SPOTIFY_CLIENT_ID,
        client_secret=settings.SPOTIFY_CLIENT_SECRET,
        redirect_uri=settings.SPOTIFY_REDIRECT_URI,
        scope=settings.SPOTIFY_SCOPE,
        cache_handler=cache_handler
    )
    return spotipy.Spotify(auth_manager=auth_manager)


@tiered_cache('spotify_search_tracks', timeout=900) # Cache for 15 minutes
def search_tracks(sp, query, limit=10):
    # Cache key will be based on sp (or rather, its auth token if cache is user-specific)
    # and query arguments. The tiered_cache decorator handles this.
    try:
        results = sp.search(q=query, type='track', limit=limit)
        tracks = []

        for spotify_track in results['tracks']['items']:
            track = get_or_create_track(spotify_track, sp)
            if track:
                tracks.append(track)
        return tracks
    except Exception as e:
        print(f"Error searching tracks: {str(e)}")
        return []


def get_or_create_track(track_data, sp: Spotify):
    spotify_id = track_data['id']
    if not spotify_id:
        return None

    # First check if track already exists
    try:
        # Try to get the track first
        track = Track.objects.get(spotify_id=spotify_id)
        return track
    except Track.DoesNotExist:
        # Only create a new track if it doesn't exist
        artists = []
        for artist_data in track_data.get('artists', []):
            artist, _ = Artist.objects.get_or_create(
                spotify_id=artist_data['id'],
                defaults={'name': artist_data['name']}
            )
            artists.append(artist)

        artist_ids = [artist.spotify_id for artist in artists if artist.spotify_id]
        genres = set()
        if artist_ids:
            try:
                spotify_artists = sp.artists(artist_ids).get('artists', [])
                for spotify_artist, artist in zip(spotify_artists, artists):
                    artist_genres = [
                        Genre.objects.get_or_create(name=genre_name)[0]
                        for genre_name in spotify_artist.get('genres', [])
                    ]
                    artist.genres.set(artist_genres)
                    artist.save()
                    genres.update(spotify_artist.get('genres', []))
            except Exception as e:
                print(f"Error fetching artist details: {e}")

        # genre, _ = Genre.objects.get_or_create(name=next(iter(genres), 'Unknown'))
        genre, _ = Genre.objects.get_or_create(name=next(iter(genres), 'Unknown'))

        release_date = None
        release_precision = track_data['album'].get('release_date_precision')
        release_date_str = track_data['album'].get('release_date')

        if release_date_str and release_precision:
            try:
                if release_precision == 'day':
                    release_date = datetime.strptime(release_date_str, '%Y-%m-%d')
                elif release_precision == 'year':
                    release_date = datetime.strptime(release_date_str, '%Y')
            except ValueError:
                release_date = None

        # Create track with a single query rather than creating and then updating
        track = Track.objects.create(
            title=track_data.get('name'),
            genres=genre,
            spotify_id=spotify_id,
            album=track_data['album'].get('name'),
            duration=timedelta(milliseconds=track_data.get('duration_ms', 0)),
            preview_url=track_data.get('preview_url'),
            image_url=track_data['album'].get('images')[0].get('url') if track_data['album'].get('images') else None,
            popularity=track_data.get('popularity', 0),
            release_date=release_date,
            audio_features={}
        )
        track.artists.set(artists)
        # track.save() # Save is called after attempting to fetch Spotify audio features

        # Attempt to fetch and store Spotify audio features
        if not track.audio_features and sp:
            try:
                spotify_features_list = sp.audio_features(tracks=[track.spotify_id])
                if spotify_features_list and spotify_features_list[0]:
                    sf = spotify_features_list[0]
                    # Select relevant features from Spotify
                    # Common features: tempo, energy, danceability, valence, acousticness, instrumentalness, liveness, speechiness, loudness
                    track.audio_features = {
                        'source': 'spotify', # Indicate the source
                        'tempo': sf.get('tempo'),
                        'energy': sf.get('energy'),
                        'danceability': sf.get('danceability'),
                        'valence': sf.get('valence'),
                        'acousticness': sf.get('acousticness'),
                        'instrumentalness': sf.get('instrumentalness'),
                        'liveness': sf.get('liveness'),
                        'speechiness': sf.get('speechiness'),
                        'loudness': sf.get('loudness'),
                        'mode': sf.get('mode'),
                        'key': sf.get('key'),
                        'time_signature': sf.get('time_signature'),
                        # Add any other desired features from Spotify
                    }
                    print(f"Successfully fetched Spotify audio features for track {track.spotify_id}")
                else:
                    print(f"No Spotify audio features returned for track {track.spotify_id}")
                    # Optionally, mark that Spotify features were checked but not found
                    # track.audio_features = {'source': 'spotify', 'found': False}
            except Exception as e:
                print(f"Error fetching Spotify audio features for track {track.spotify_id}: {str(e)}")

        track.save()
        return track


def update_song_custom_audio_features(song, audio_features): # Renamed function
    # Ensure audio_features is not None and is a dictionary
    if song.audio_features is None:
        song.audio_features = {}

    song.audio_features.update({
        'source': 'librosa', # Indicate the source
        "tempo": audio_features.get('tempo'), # Use .get for safety
        "chroma_stft_mean": audio_features.get('chroma_stft_mean'),
        "rmse_mean": audio_features.get('rmse_mean'),
        "spectral_centroid_mean": audio_features.get('spectral_centroid_mean'),
        "spectral_bandwidth_mean": audio_features.get('spectral_bandwidth_mean'),
        "rolloff_mean": audio_features.get('rolloff_mean'),
        "zero_crossing_rate_mean": audio_features.get('zero_crossing_rate_mean'),
        "mfcc_mean": audio_features.get('mfcc_mean')
        # Add any other Librosa features that are extracted
    })
    song.save()


def get_or_create_playlist(playlist_id, request, sp: Spotify):
    playlist = Playlist.objects.filter(spotify_id=playlist_id).first()
    if not playlist:
        playlist_data = sp.playlist(playlist_id)
        playlist = Playlist.objects.create(
            user=request.user,
            name=playlist_data['name'],
            spotify_id=playlist_data['id'],
            description=playlist_data['description'],
            image_url=playlist_data['images'][0]['url'] if playlist_data['images'] else None,
        )
        playlist.save()

    return playlist


@tiered_cache('user_playlists', timeout=3600)
def get_user_playlists(sp: Spotify, request):
    playlists = sp.current_user_playlists()

    # Define a worker function to process each playlist in parallel
    def process_playlist(playlist):
        return get_or_create_playlist(playlist['id'], request, sp)

    # Process playlists in parallel using ThreadPoolExecutor
    result = []
    with ThreadPoolExecutor(max_workers=min(10, len(playlists['items']))) as executor:
        future_to_playlist = {executor.submit(process_playlist, playlist): playlist
                              for playlist in playlists['items']}

        for future in concurrent.futures.as_completed(future_to_playlist):
            pl = future.result()
            if pl:
                result.append(pl)

    return result


@tiered_cache('playlist_tracks', timeout=3600)
def get_playlist_tracks(sp: Spotify, playlist_id):
    results = sp.playlist_items(playlist_id)

    # Define a worker function to process each track in parallel
    def process_track(spotify_track):
        return get_or_create_track(spotify_track['track'], sp)

    # Process tracks in parallel using ThreadPoolExecutor
    tracks = []
    with ThreadPoolExecutor(max_workers=min(10, max(len(results['items']), 10))) as executor:
        future_to_track = {executor.submit(process_track, spotify_track): spotify_track
                           for spotify_track in results['items']}

        for future in concurrent.futures.as_completed(future_to_track):
            track = future.result()
            if track:
                tracks.append(track)

    return tracks


@tiered_cache('user_top_tracks', timeout=3600)
def get_user_top_tracks(sp: Spotify):
    results = sp.current_user_top_tracks(limit=15, time_range='medium_term')

    # Define a worker function to process each track in parallel
    def process_track(spotify_track):
        track = get_or_create_track(spotify_track, sp)
        if track:
            return {
                'name': track.title,
                'artist': track.artists.all().first().name,
                'album': track.album,
                'id': track.spotify_id
            }
        return None

    # Process tracks in parallel using ThreadPoolExecutor
    top_tracks = []
    with ThreadPoolExecutor(max_workers=min(10, len(results['items']))) as executor:
        future_to_track = {executor.submit(process_track, spotify_track): spotify_track
                           for spotify_track in results['items']}

        for future in concurrent.futures.as_completed(future_to_track):
            song_dict = future.result()
            if song_dict:
                top_tracks.append(song_dict)

    return top_tracks


@tiered_cache('user_recently_played', timeout=3600)
def get_user_recently_played(sp: Spotify):
    results = sp.current_user_recently_played(limit=15)

    # Define a worker function to process each track in parallel
    def process_track(spotify_track):
        track = get_or_create_track(spotify_track['track'], sp)
        if track:
            return {
                'name': track.title,
                'artist': track.artists.all().first().name,
                'album': track.album,
                'id': track.spotify_id,
                'played_at': spotify_track['played_at'],
                'duration': spotify_track['track']['duration_ms']  # Add duration in milliseconds
            }
        return None

    # Process tracks in parallel using ThreadPoolExecutor
    recently_played = []
    with ThreadPoolExecutor(max_workers=min(10, len(results['items']))) as executor:
        future_to_track = {executor.submit(process_track, spotify_track): spotify_track
                           for spotify_track in results['items']}

        for future in concurrent.futures.as_completed(future_to_track):
            track_dict = future.result()
            if track_dict:
                recently_played.append(track_dict)

    return recently_played


@tiered_cache('spotify_artist_top_tracks', timeout=3600)
def get_artist_top_tracks(sp, artist_id):
    results = sp.artist_top_tracks(artist_id)
    return results['tracks']


@tiered_cache('spotify_artist_details', timeout=3600) # Cache for 1 hour
def get_artist_details(sp, artist_id):
    return sp.artist(artist_id)


@tiered_cache('spotify_artist_albums', timeout=3600) # Cache for 1 hour
def get_artist_albums(sp, artist_id, album_type='album', limit=5):
    return sp.artist_albums(artist_id, album_type=album_type, limit=limit)['items']


@tiered_cache('jiosaavn_search', timeout=3600)
def search_jiosaavn(query, limit=10):
    try:
        url = f"https://www.jiosaavn.com/api.php?__call=autocomplete.get&_format=json&_marker=0&cc=in&includeMetaTags=1&query={query}"
        response = requests.get(url, timeout=10)

        if response.status_code != 200:
            print(f"JioSaavn API error: Status code {response.status_code}")
            return []

        data = response.json()

        if not data or 'songs' not in data or 'data' not in data.get('songs', {}):
            return []

        # Define a worker function to process each song in parallel
        def process_song(song):
            try:
                song_details = get_track_details_jiosaavn(song['id'])
                if song_details:
                    return {
                        'id': song['id'],
                        'name': song['title'],
                        'artist': song_details['artist'],
                        'album': song_details['album'],
                        'year': song_details['year'],
                        'image_url': song_details['image_url'],
                        'duration': song_details['duration'],
                        'preview_url': song_details['preview_url'],
                    }
            except Exception as e:
                print(f"Error processing JioSaavn track {song.get('id')}: {str(e)}")
                return None

        # Process songs in parallel using ThreadPoolExecutor
        tracks = []
        songs_to_process = data['songs']['data'][:limit]

        with ThreadPoolExecutor(max_workers=min(10, len(songs_to_process))) as executor:
            future_to_song = {executor.submit(process_song, song): song
                              for song in songs_to_process}

            for future in concurrent.futures.as_completed(future_to_song):
                track = future.result()
                if track:
                    tracks.append(track)

        return tracks
    except Exception as e:
        print(f"Error searching JioSaavn: {str(e)}")
        return []


@tiered_cache('jiosaavn_track', timeout=3600)
def get_track_details_jiosaavn(track_id):
    try:
        url = f"https://www.jiosaavn.com/api.php?__call=song.getDetails&cc=in&_marker=0%3F_marker%3D0&_format=json&pids={track_id}"
        response = requests.get(url, timeout=10)

        if response.status_code != 200:
            print(f"JioSaavn API error: Status code {response.status_code}")
            return None

        data = response.json()

        if not data or track_id not in data:
            return None

        data = data[track_id]

        return {
            'id': data['id'],
            'name': data['song'],
            'artist': data['primary_artists'],
            'album': data['album'],
            'year': data['year'],
            'image_url': re.sub(r'\d+x\d+', '500x500', data['image']),
            'duration': int(data['duration']) * 1000,
            'preview_url': data['vlink'] if data.get('vlink') else data.get('media_preview_url', '')
        }
    except Exception as e:
        print(f"Error getting JioSaavn track details: {str(e)}")
        return None


@tiered_cache(maxsize=100)
def extract_audio_features(audio_file):
    try:
        y, sr = librosa.load(audio_file, duration=30, res_type='kaiser_fast')
    except Exception as e:
        print(f"Error loading audio file: {e}")
        return None

    # Extract features
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    chroma_stft = librosa.feature.chroma_stft(y=y, sr=sr)
    rmse = librosa.feature.rms(y=y)
    spec_cent = librosa.feature.spectral_centroid(y=y, sr=sr)
    spec_bw = librosa.feature.spectral_bandwidth(y=y, sr=sr)
    rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)
    zcr = librosa.feature.zero_crossing_rate(y)
    mfcc = librosa.feature.mfcc(y=y, sr=sr)

    return {
        'tempo': float(tempo),
        'chroma_stft_mean': float(np.mean(chroma_stft)),
        'rmse_mean': float(np.mean(rmse)),
        'spectral_centroid_mean': float(np.mean(spec_cent)),
        'spectral_bandwidth_mean': float(np.mean(spec_bw)),
        'rolloff_mean': float(np.mean(rolloff)),
        'zero_crossing_rate_mean': float(np.mean(zcr)),
        'mfcc_mean': float(np.mean(mfcc)),
    }


# Optimized implementation
def download_preview(preview_url, track_id):
    if not preview_url or not preview_url.startswith("http"):
        return None

    file_path = os.path.join(settings.MEDIA_ROOT, 'previews', f'{track_id}.mp3')
    if os.path.exists(file_path):
        return file_path

    try:
        response = requests.get(preview_url, stream=True, timeout=10)
        response.raise_for_status()

        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return file_path
    except (requests.RequestException, IOError) as e:
        print(f"Error downloading preview for {track_id}: {str(e)}")
        return None


# @tiered_cache('recommendations', timeout=3600)
# def get_recommendations(track_id, stored_tracks, limit=10):
#     track = Track.objects.get(spotify_id=track_id)
#
#     # First check if we already have audio features
#     if track.audio_features:
#         target_features = track.audio_features
#     else:
#         # If no features, extract them
#         # search = f"{track.title} {",".join([artist.name for artist in track.artists.all()])} {track.album}".strip()
#         # search = f"{track.title} {track.artists.all().first().name}"
#         # translated_text = translate_text(search)
#         try:
#             search = f"{track.title} {track.artists.all().first().name}"
#             search_results = search_jiosaavn(search)
#
#             if not search_results:
#                 return []
#
#             search_song = search_results[0]
#             target_track = get_track_details_jiosaavn(search_song['id'])
#
#             if not target_track:
#                 return []
#
#             if not track.preview_url and target_track.get('preview_url'):
#                 track.preview_url = target_track['preview_url']
#                 track.save()
#
#             preview_file = download_preview(target_track['preview_url'], track_id)
#             if not preview_file:
#                 return []
#
#             target_features = extract_audio_features(preview_file)
#             if not target_features:
#                 return []
#
#             track.audio_features = target_features
#             track.save()
#         except Exception as e:
#             print(f"Error extracting audio features: {str(e)}")
#             return []
#
#     target_features_scalar = {k: float(v) for k, v in target_features.items()}
#
#     similarities = []
#     for stored_track in stored_tracks:
#         try:
#             stored_song = Track.objects.get(spotify_id=stored_track['id'])
#             if stored_song.audio_features:
#                 stored_features = stored_song.audio_features
#             else:
#                 raise Track.DoesNotExist  # Handle like song not found
#         except Track.DoesNotExist:
#             search = f"{stored_track['name']} {stored_track['artist']}"
#             results = search_jiosaavn(search)
#             if not results:
#                 continue
#
#             preview_path = download_preview(results[0]['preview_url'], stored_track['id'])
#             if not preview_path:
#                 continue
#
#             stored_features = extract_audio_features(preview_path)
#
#             # Save features if song exists
#             try:
#                 stored_song = Track.objects.get(spotify_id=stored_track['id'])
#                 stored_song.audio_features = stored_features
#                 stored_song.save()
#             except Track.DoesNotExist:
#                 pass
#
#         stored_features_scalar = {k: float(v) for k, v in stored_features.items()}
#         target_vector = np.array(list(target_features_scalar.values()))
#         stored_vector = np.array(list(stored_features_scalar.values()))
#
#         similarity = cosine_similarity(
#             target_vector.reshape(1, -1),
#             stored_vector.reshape(1, -1)
#         )[0][0]
#
#         similarities.append({
#             'id': stored_track['id'],
#             'similarity': similarity
#         })
#
#     recommendations = sorted(similarities, key=lambda x: x['similarity'], reverse=True)[:limit]
#     return recommendations
# Standard feature keys for cosine similarity.
# These are primarily based on Spotify's audio features for easier direct use.
# Librosa features will be mapped/approximated to these.
STANDARD_SIMILARITY_FEATURES = [
    'acousticness', 'danceability', 'energy', 'instrumentalness',
    'liveness', 'loudness', 'speechiness', 'tempo', 'valence', 'mode', 'key'
]

def _normalize_loudness(loudness_db):
    """Normalizes loudness from dB (e.g., -60 to 0) to a 0-1 scale."""
    if loudness_db is None:
        return 0.5 # Default if missing
    return max(0.0, min(1.0, (loudness_db + 60) / 60))

def _normalize_tempo(tempo_bpm):
    """Normalizes tempo (e.g., 50-250 BPM) to a 0-1 scale."""
    if tempo_bpm is None:
        return 0.5 # Default if missing
    # Assuming a typical BPM range, this is a simplified normalization
    return max(0.0, min(1.0, (tempo_bpm - 50) / 200))


def _get_standardized_features_for_track(track_obj, sp_client):
    """
    Fetches/extracts audio features for a track_obj, standardizes them, and saves if newly fetched.
    Priority: DB -> Spotify API -> Librosa.
    Returns a list of feature values in the order of STANDARD_SIMILARITY_FEATURES, or None.
    """
    features_to_standardize = None
    source_of_features = None

    if track_obj.audio_features and isinstance(track_obj.audio_features, dict) and 'source' in track_obj.audio_features:
        features_to_standardize = track_obj.audio_features
        source_of_features = track_obj.audio_features['source']
        print(f"Using existing features for track {track_obj.spotify_id} from source: {source_of_features}")

    # Try Spotify API if no features or if existing features are not from Spotify (or spotify_attempted/error)
    if not features_to_standardize or source_of_features not in ['spotify', 'librosa']: # Allow reprocessing if 'spotify_attempted' etc.
        if track_obj.spotify_id and sp_client:
            try:
                print(f"Attempting to fetch Spotify features for track {track_obj.spotify_id}...")
                spotify_api_features_list = sp_client.audio_features(tracks=[track_obj.spotify_id])
                if spotify_api_features_list and spotify_api_features_list[0]:
                    sf = spotify_api_features_list[0]
                    track_obj.audio_features = {
                        'source': 'spotify',
                        'tempo': sf.get('tempo'), 'energy': sf.get('energy'),
                        'danceability': sf.get('danceability'), 'valence': sf.get('valence'),
                        'acousticness': sf.get('acousticness'), 'instrumentalness': sf.get('instrumentalness'),
                        'liveness': sf.get('liveness'), 'speechiness': sf.get('speechiness'),
                        'loudness': sf.get('loudness'), 'mode': sf.get('mode'), 'key': sf.get('key'),
                        'time_signature': sf.get('time_signature'),
                    }
                    track_obj.save()
                    features_to_standardize = track_obj.audio_features
                    source_of_features = 'spotify'
                    print(f"Fetched and saved Spotify features for track {track_obj.spotify_id}")
                else:
                    # Mark that Spotify API was tried but returned no features
                    track_obj.audio_features = {'source': 'spotify_no_data'}
                    track_obj.save()
                    print(f"Spotify API returned no audio features for track {track_obj.spotify_id}")
            except Exception as e:
                print(f"Error fetching Spotify features for {track_obj.spotify_id}: {str(e)}")
                track_obj.audio_features = {'source': 'spotify_error', 'error': str(e)} # Log error state
                track_obj.save()

    # Fallback to Librosa if still no usable features (Spotify failed or track is not on Spotify)
    if not features_to_standardize or source_of_features not in ['spotify', 'librosa']:
        print(f"Attempting Librosa feature extraction for track {track_obj.spotify_id} ({track_obj.title})...")
        preview_url_to_use = track_obj.preview_url
        if not preview_url_to_use:
            try:
                artist_name_for_search = track_obj.artists.first().name if track_obj.artists.exists() else ""
                search_query = f"{track_obj.title} {artist_name_for_search}".strip()
                jiosaavn_results = search_jiosaavn(search_query, limit=1) # This is cached
                if jiosaavn_results and jiosaavn_results[0].get('preview_url'):
                    preview_url_to_use = jiosaavn_results[0]['preview_url']
            except Exception as e:
                print(f"Error finding JioSaavn preview for {track_obj.spotify_id}: {str(e)}")

        if preview_url_to_use:
            preview_file_path = download_preview(preview_url_to_use, track_obj.spotify_id)
            if preview_file_path:
                librosa_features = extract_audio_features(preview_file_path) # Librosa core call
                if librosa_features:
                    update_song_custom_audio_features(track_obj, librosa_features) # Saves with 'source': 'librosa'
                    features_to_standardize = track_obj.audio_features
                    source_of_features = 'librosa'
                    print(f"Extracted and saved Librosa features for track {track_obj.spotify_id}")
        else:
            print(f"No preview URL for Librosa for track {track_obj.spotify_id}")

    # If after all attempts, no features, return None
    if not features_to_standardize or source_of_features not in ['spotify', 'librosa']:
        print(f"Failed to obtain/extract any usable audio features for track {track_obj.spotify_id}")
        return None

    # Standardize the features into a vector
    feature_vector = []
    if source_of_features == 'spotify':
        for key in STANDARD_SIMILARITY_FEATURES:
            val = features_to_standardize.get(key)
            if key == 'loudness': val = _normalize_loudness(val)
            elif key == 'tempo': val = _normalize_tempo(val)
            elif key == 'key': val = float(val / 11) if val is not None else 0.5 # Normalize key (0-11)
            elif key == 'mode': val = float(val) if val is not None else 0.5 # Mode is 0 or 1
            # For other features, assume they are already 0-1 or use as is if direct mapping.
            feature_vector.append(float(val) if val is not None else 0.5) # Default 0.5 for missing
    elif source_of_features == 'librosa':
        # Map Librosa features to STANDARD_SIMILARITY_FEATURES
        # This mapping is crucial and needs to be refined based on feature characteristics
        # Using simplified mapping for now.
        feature_vector.append(_normalize_tempo(features_to_standardize.get('tempo'))) # tempo
        feature_vector.append(features_to_standardize.get('rmse_mean', 0.5)) # energy (proxy)
        feature_vector.append(0.5) # danceability (placeholder)
        feature_vector.append(0.5) # valence (placeholder)
        feature_vector.append(features_to_standardize.get('zero_crossing_rate_mean', 0.5)) # acousticness (crude proxy)
        feature_vector.append(0.5) # instrumentalness (placeholder)
        feature_vector.append(0.5) # liveness (placeholder)
        feature_vector.append(features_to_standardize.get('spectral_bandwidth_mean', 0.5)) # speechiness (crude proxy from bandwidth)
        feature_vector.append(features_to_standardize.get('rmse_mean', 0.5)) # loudness (proxy, not normalized like dB)
        feature_vector.append(0.5) # mode (placeholder)
        feature_vector.append(0.5) # key (placeholder)
        # Trim or pad to match length of STANDARD_SIMILARITY_FEATURES
        feature_vector = (feature_vector + [0.5] * len(STANDARD_SIMILARITY_FEATURES))[:len(STANDARD_SIMILARITY_FEATURES)]
    else:
        return None # Should not happen if previous checks are correct

    return feature_vector


@tiered_cache('recommendations', timeout=3600)
def get_recommendations(track_id, stored_tracks_data, limit=10, sp_client=None):
    # Ensure sp_client is available if not passed (e.g. from a non-request context)
    # This is tricky; for now, assume sp_client is available or get_spotify_client can be adapted.
    # For this subtask, we'll assume sp_client is passed if Spotify interaction is needed.
    # If sp_client is None, Spotify features won't be fetched.

    try:
        target_track_obj = Track.objects.select_related('genres').prefetch_related('artists').get(spotify_id=track_id)
    except Track.DoesNotExist:
        print(f"Target track {track_id} not found in DB for recommendations.")
        return []

    target_feature_vector = _get_standardized_features_for_track(target_track_obj, sp_client)
    if not target_feature_vector:
        print(f"Could not get standardized features for target track {track_id}.")
        return []

    # Fetch all stored_track objects from DB to avoid N+1 in loop
    stored_track_ids = [st['id'] for st in stored_tracks_data if st['id'] != target_track_obj.spotify_id] # Exclude target itself
    db_stored_tracks = Track.objects.filter(spotify_id__in=stored_track_ids)\
                                  .select_related('genres').prefetch_related('artists')
    db_stored_tracks_map = {t.spotify_id: t for t in db_stored_tracks}

    similarities = []
    for input_track_data in stored_tracks_data:
        current_track_id = input_track_data['id']
        if current_track_id == target_track_obj.spotify_id:
            continue

        stored_track_obj = db_stored_tracks_map.get(current_track_id)
        if not stored_track_obj:
            # This might happen if stored_tracks_data contains IDs not in DB (e.g., from live Spotify search results)
            # Create a temporary Track-like object or fetch it. For simplicity, skip if not in map.
            print(f"Stored track {current_track_id} not found in pre-fetched DB map. Skipping.")
            continue

        stored_feature_vector = _get_standardized_features_for_track(stored_track_obj, sp_client)
        if not stored_feature_vector:
            print(f"Could not get standardized features for stored track {current_track_id}. Skipping.")
            continue

        # Cosine similarity
        target_np = np.array(target_feature_vector).reshape(1, -1)
        stored_np = np.array(stored_feature_vector).reshape(1, -1)

        similarity = cosine_similarity(target_np, stored_np)[0][0]
        similarities.append({'id': current_track_id, 'similarity': similarity})

    recommendations = sorted(similarities, key=lambda x: x['similarity'], reverse=True)[:limit]
    return recommendations


def batch_extract_audio_features(audio_files):
    """
    Extract audio features for multiple files in batch

    Args:
        audio_files: List of (track_id, file_path) tuples

    Returns:
        Dictionary mapping track_ids to their extracted features
    """
    results = {}

    # Process in parallel using ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(8, len(audio_files))) as executor:
        future_to_file = {
            executor.submit(extract_audio_features, file_path): track_id
            for track_id, file_path in audio_files
        }

        for future in concurrent.futures.as_completed(future_to_file):
            track_id = future_to_file[future]
            try:
                features = future.result()
                if features:
                    results[track_id] = features
            except Exception as e:
                print(f"Error extracting features for {track_id}: {str(e)}")

    return results


def create_playlist_spotify(sp: Spotify, name, description=""):
    user_id = sp.current_user()['id']
    playlist = sp.user_playlist_create(user_id, name, public=False, description=description)
    return playlist


def add_tracks_to_playlist_spotify(sp: Spotify, playlist_id, track_ids):
    track_ids = [f"spotify:track:{track_id}" for track_id in track_ids]
    sp.playlist_add_items(playlist_id, track_ids)


def remove_tracks_from_playlist_spotify(sp: Spotify, playlist_id, track_ids):
    track_ids = [f"spotify:track:{track_id}" for track_id in track_ids]
    sp.playlist_remove_all_occurrences_of_items(playlist_id, track_ids)


def delete_playlist_spotify(sp: Spotify, playlist_id):
    sp.current_user_unfollow_playlist(playlist_id)


@tiered_cache('listening_time', timeout=3600)
def calculate_listening_time(sp: Spotify, recently_played):
    if not recently_played:
        return 0.0
    total_ms = sum(track['duration'] for track in recently_played) / (1000 * 60 * 60)  # Convert to hours
    return total_ms


@tiered_cache('favorite_genre', timeout=3600)
def get_favorite_genre(sp: Spotify, top_tracks):
    if not top_tracks:
        return None
    artist_names = [track['artist'] for track in top_tracks]
    all_genres = []
    missing_artists = []
    artist_query = Q()
    for name in artist_names:
        artist_query |= Q(name__iexact=name)
    existing_artists = Artist.objects.filter(artist_query).prefetch_related('genres')
    artist_genres_map = {artist.name.lower(): list(artist.genres.values_list('name', flat=True))
                         for artist in existing_artists}

    for artist_name in artist_names:
        if artist_name.lower() in artist_genres_map:
            all_genres.extend(artist_genres_map[artist_name.lower()])
        else:
            missing_artists.append(artist_name)

    if missing_artists:
        try:
            # Define a worker function to fetch artist genres in parallel
            def fetch_artist_genres(artist_name):
                try:
                    search_results = sp.search(artist_name, type='artist', limit=1)
                    if not search_results['artists']['items']:
                        return []

                    artist_data = search_results['artists']['items'][0]
                    genres = artist_data.get('genres', [])

                    artist, created = Artist.objects.get_or_create(
                        spotify_id=artist_data['id'],
                        defaults={'name': artist_data['name']}
                    )

                    if created or not artist.genres.exists():
                        genre_objects = [
                            Genre.objects.get_or_create(name=genre_name)[0]
                            for genre_name in genres
                        ]
                        artist.genres.set(genre_objects)

                    return genres
                except Exception as e:
                    print(f"Error fetching genres for {artist_name}: {str(e)}")
                    return []

            # Process artists in parallel using ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(10, len(missing_artists))) as executor:
                future_to_artist = {executor.submit(fetch_artist_genres, artist_name): artist_name
                                    for artist_name in missing_artists}

                for future in concurrent.futures.as_completed(future_to_artist):
                    genres = future.result()
                    all_genres.extend(genres)

        except Exception as e:
            print(f"Error fetching artist genres from Spotify: {e}")

    if not all_genres:
        return None

    most_common_genre = Counter(all_genres).most_common(1)[0][0]

    return most_common_genre
