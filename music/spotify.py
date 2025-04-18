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


def search_tracks(sp, query, limit=10):
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
        track.save()
        return track


def update_song_audio_features(song, audio_features):
    song.audio_features.update({
        "tempo": audio_features['tempo'],
        "chroma_stft_mean": audio_features['chroma_stft_mean'],
        "rmse_mean": audio_features['rmse_mean'],
        "spectral_centroid_mean": audio_features['spectral_centroid_mean'],
        "spectral_bandwidth_mean": audio_features['spectral_bandwidth_mean'],
        "rolloff_mean": audio_features['rolloff_mean'],
        "zero_crossing_rate_mean": audio_features['zero_crossing_rate_mean'],
        "mfcc_mean": audio_features['mfcc_mean']
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
@tiered_cache('recommendations', timeout=3600)
def get_recommendations(track_id, stored_tracks, limit=10):
    track = Track.objects.get(spotify_id=track_id)

    # Prepare a list to collect tracks that need audio feature extraction
    tracks_to_process = []
    target_preview_file = None

    # First check if the target track needs feature extraction
    if not track.audio_features:
        # If no features, extract them
        try:
            search = f"{track.title} {track.artists.all().first().name}"
            search_results = search_jiosaavn(search)

            if not search_results:
                return []

            search_song = search_results[0]
            target_track = get_track_details_jiosaavn(search_song['id'])

            if not target_track:
                return []

            if not track.preview_url and target_track.get('preview_url'):
                track.preview_url = target_track['preview_url']
                track.save()

            target_preview_file = download_preview(target_track['preview_url'], track_id)
            if not target_preview_file:
                return []

            # Add target track to the list of tracks to process
            tracks_to_process.append((track_id, target_preview_file))
        except Exception as e:
            print(f"Error preparing target track for feature extraction: {str(e)}")
            return []
    else:
        target_features = track.audio_features

    # Collect stored tracks that need feature extraction
    tracks_needing_features = []
    stored_track_map = {}

    for stored_track in stored_tracks:
        stored_track_map[stored_track['id']] = stored_track
        try:
            stored_song = Track.objects.get(spotify_id=stored_track['id'])
            if stored_song.audio_features:
                continue  # Skip tracks that already have features
        except Track.DoesNotExist:
            pass

        # Track needs features extraction
        search = f"{stored_track['name']} {stored_track['artist']}"
        results = search_jiosaavn(search)
        if not results:
            continue

        preview_path = download_preview(results[0]['preview_url'], stored_track['id'])
        if not preview_path:
            continue

        tracks_to_process.append((stored_track['id'], preview_path))
        tracks_needing_features.append(stored_track['id'])

    # Extract features in batch if there are tracks to process
    extracted_features = {}
    if tracks_to_process:
        extracted_features = batch_extract_audio_features(tracks_to_process)

        # Update target track features if needed
        if track_id in extracted_features:
            target_features = extracted_features[track_id]
            track.audio_features = target_features
            track.save()

        # Update stored tracks with extracted features
        for track_id in tracks_needing_features:
            if track_id in extracted_features:
                try:
                    stored_song = Track.objects.get(spotify_id=track_id)
                    stored_song.audio_features = extracted_features[track_id]
                    stored_song.save()
                except Track.DoesNotExist:
                    pass

    # If we still don't have target features, return empty list
    if not track.audio_features and track_id not in extracted_features:
        return []

    # Use the target features (either from DB or newly extracted)
    if track_id in extracted_features:
        target_features = extracted_features[track_id]

    target_features_scalar = {k: float(v) for k, v in target_features.items()}

    # Calculate similarities
    similarities = []
    for stored_track in stored_tracks:
        track_id = stored_track['id']
        try:
            # Get features either from extracted batch or from database
            if track_id in extracted_features:
                stored_features = extracted_features[track_id]
            else:
                stored_song = Track.objects.get(spotify_id=track_id)
                if stored_song.audio_features:
                    stored_features = stored_song.audio_features
                else:
                    continue  # Skip if no features available

            stored_features_scalar = {k: float(v) for k, v in stored_features.items()}
            target_vector = np.array(list(target_features_scalar.values()))
            stored_vector = np.array(list(stored_features_scalar.values()))

            similarity = cosine_similarity(
                target_vector.reshape(1, -1),
                stored_vector.reshape(1, -1)
            )[0][0]

            similarities.append({
                'id': track_id,
                'similarity': similarity
            })
        except Exception as e:
            print(f"Error calculating similarity for track {track_id}: {str(e)}")
            continue

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
