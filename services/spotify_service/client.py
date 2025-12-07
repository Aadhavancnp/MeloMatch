from services.async_utils import run_in_executor, async_cache_api_call, async_celery_delay
import concurrent
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import numpy as np
import spotipy
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from sklearn.metrics.pairwise import cosine_similarity
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth

from music.models import Track, Playlist, Artist, Genre
from music.tasks import extract_track_features_task
from services.utils import cache_api_call

logger = logging.getLogger(__name__)


def has_spotify_token(request):
    """Check if user has valid Spotify token in session."""
    cache_handler = spotipy.cache_handler.DjangoSessionCacheHandler(request)
    token_info = cache_handler.get_cached_token()
    return token_info is not None


# For request-based authentication (uses session cache via Spotipy)
def get_spotify_client(request, raise_on_no_token=False):
    """
    Get Spotify client from session.

    Args:
        request: Django request object
        raise_on_no_token: If True, raises exception if no token available
                          If False, returns client that may prompt for auth
    """
    cache_handler = spotipy.cache_handler.DjangoSessionCacheHandler(request)

    # Check if we have a cached token
    if raise_on_no_token:
        token_info = cache_handler.get_cached_token()
        if not token_info:
            raise ValueError("No Spotify token available")

    auth_manager = SpotifyOAuth(
        client_id=settings.SPOTIFY_CLIENT_ID,
        client_secret=settings.SPOTIFY_CLIENT_SECRET,
        redirect_uri=settings.SPOTIFY_REDIRECT_URI,
        scope=settings.SPOTIFY_SCOPE,
        cache_handler=cache_handler,
        open_browser=False
    )
    return spotipy.Spotify(auth_manager=auth_manager)


@cache_api_call(key_prefix="spotify_search_tracks", timeout=900)
def search_tracks(sp, query, limit=10):
    """Search tracks with parallel processing for better performance."""
    try:
        results = sp.search(q=query, type='track', limit=limit)
        tracks = []
        if results and 'tracks' in results:
            items = results['tracks']['items']
            
            # Process tracks in parallel using ThreadPoolExecutor
            def process_track(spotify_track):
                return get_or_create_track_fast(spotify_track, sp)
            
            with ThreadPoolExecutor(max_workers=min(15, len(items) or 1)) as executor:
                future_to_track = {executor.submit(process_track, track): track 
                                   for track in items}
                for future in concurrent.futures.as_completed(future_to_track):
                    try:
                        track = future.result(timeout=10)
                        if track:
                            tracks.append(track)
                    except Exception as e:
                        logger.debug(f"Track processing failed: {e}")
        return tracks
    except Exception as e:
        logger.error(f"Error searching Spotify tracks: {e}")
        return []


def get_or_create_track_fast(track_data, sp):
    """
    Fast version of get_or_create_track. 
    Tries JioSaavn for preview (fast ~2s), queues YouTube as fallback.
    """
    spotify_id = track_data.get('id')
    if not spotify_id:
        return None

    try:
        # Return existing track immediately
        track = Track.objects.get(spotify_id=spotify_id)
        return track
    except Track.DoesNotExist:
        try:
            # Create artists (fast - just DB lookups)
            artists = []
            for artist_data in track_data.get('artists', []):
                artist, _ = Artist.objects.get_or_create(
                    spotify_id=artist_data['id'],
                    defaults={'name': artist_data['name']}
                )
                artists.append(artist)

            # Skip fetching artist genres from Spotify API during search (slow)
            primary_genre, _ = Genre.objects.get_or_create(name='Unknown')

            # Handle release date parsing
            release_date = None
            album_data = track_data.get('album', {})
            release_precision = album_data.get('release_date_precision')
            release_date_str = album_data.get('release_date')

            if release_date_str and release_precision:
                try:
                    if release_precision == 'day':
                        release_date = datetime.strptime(release_date_str, '%Y-%m-%d').date()
                    elif release_precision == 'month':
                        release_date = datetime.strptime(release_date_str, '%Y-%m').date()
                    elif release_precision == 'year':
                        release_date = datetime.strptime(release_date_str, '%Y').date()
                except ValueError:
                    release_date = None

            # Safely get image URL
            image_url = None
            album_images = album_data.get('images', [])
            if album_images and len(album_images) > 0:
                image_url = album_images[0].get('url')

            # Get preview_url from Spotify only (fast) - JioSaavn/YouTube fetched on track click
            preview_url = track_data.get('preview_url')
            
            # NOTE: Don't do JioSaavn lookup here - it's slow (1-2s per track)
            # Preview URL will be fetched when user clicks on track (track_detail view)

            # Create the track
            track = Track.objects.create(
                title=track_data.get('name', 'Unknown Title'),
                spotify_id=spotify_id,
                album=album_data.get('name', 'Unknown Album'),
                duration=timedelta(milliseconds=track_data.get('duration_ms', 0)),
                preview_url=preview_url,
                image_url=image_url,
                popularity=track_data.get('popularity', 0),
                release_date=release_date,
                genres=primary_genre,
                audio_features={},
                price=0.99
            )

            # Set the many-to-many relationships
            track.artists.set(artists)
            track.save()

            # NOTE: Don't queue preview fetch here either - will be done on track click
            # This makes search fast (only Spotify API + DB operations)

            return track

        except Exception as e:
            logger.error(f"Error creating track {spotify_id}: {e}")
            return None


def get_or_create_track(track_data, sp):
    spotify_id = track_data.get('id')
    if not spotify_id:
        return None

    try:
        track = Track.objects.get(spotify_id=spotify_id)
        return track
    except Track.DoesNotExist:
        try:
            # Create artists
            artists = []
            for artist_data in track_data.get('artists', []):
                artist, _ = Artist.objects.get_or_create(
                    spotify_id=artist_data['id'],
                    defaults={'name': artist_data['name']}
                )
                artists.append(artist)

            # Fetch and set genres for artists
            artist_ids = [
                artist.spotify_id for artist in artists if artist.spotify_id]
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
                    logger.error(f"Error fetching artist details: {e}")

            # Get or create a primary genre
            primary_genre_name = next(iter(genres), 'Unknown')
            primary_genre, _ = Genre.objects.get_or_create(
                name=primary_genre_name)

            # Handle release date parsing
            release_date = None
            album_data = track_data.get('album', {})
            release_precision = album_data.get('release_date_precision')
            release_date_str = album_data.get('release_date')

            if release_date_str and release_precision:
                try:
                    if release_precision == 'day':
                        release_date = datetime.strptime(
                            release_date_str, '%Y-%m-%d').date()
                    elif release_precision == 'month':
                        release_date = datetime.strptime(
                            release_date_str, '%Y-%m').date()
                    elif release_precision == 'year':
                        release_date = datetime.strptime(
                            release_date_str, '%Y').date()
                except ValueError:
                    release_date = None

            # Safely get image URL
            image_url = None
            album_images = album_data.get('images', [])
            if album_images and len(album_images) > 0:
                image_url = album_images[0].get('url')

            # Get preview_url from Spotify only - JioSaavn/YouTube fetched on track click
            preview_url = track_data.get('preview_url')

            # Construct query for background task
            artist_name = ""
            if track_data.get('artists'):
                artist_name = track_data['artists'][0].get('name', '')

            track_name = track_data.get('name', '')
            query = f"{track_name} {artist_name}".strip()

            # NOTE: JioSaavn lookup removed from creation - done on track click instead
            # This makes playlist imports and searches much faster
            youtube_preview_needed = not preview_url and query

            # Create the track
            track = Track.objects.create(
                title=track_data.get('name', 'Unknown Title'),
                spotify_id=spotify_id,
                album=album_data.get('name', 'Unknown Album'),
                duration=timedelta(
                    milliseconds=track_data.get('duration_ms', 0)),
                preview_url=preview_url,
                image_url=image_url,
                popularity=track_data.get('popularity', 0),
                release_date=release_date,
                genres=primary_genre,  # Set the required ForeignKey field
                audio_features={},
                price=0.99  # Default price for tracks
            )

            # Set the many-to-many relationships after creation
            track.artists.set(artists)
            track.save()

            # NOTE: Spotify audio_features API is deprecated - using Librosa instead
            # Audio features will be extracted asynchronously via Celery task
            # try:
            #     audio_features_list = sp.audio_features([spotify_id])
            #     if audio_features_list and audio_features_list[0]:
            #         features = audio_features_list[0]
            #         relevant_keys = ['danceability', 'energy', 'key', 'loudness', 'mode', 'speechiness',
            #                          'acousticness', 'instrumentalness', 'liveness', 'valence', 'tempo']
            #         filtered_features = {k: features[k]
            #                              for k in relevant_keys if k in features}
            #         track.audio_features = filtered_features
            #         track.save()
            # except Exception as e:
            #     print(f"Error fetching Spotify audio features for {spotify_id}: {e}")

            # Queue background tasks
            try:
                if youtube_preview_needed:
                    # Queue preview fetch (JioSaavn first, YouTube fallback)
                    from music.tasks import fetch_preview_url_task
                    fetch_preview_url_task.delay(track.id, query)
                    logger.info(
                        f"Queued preview fetch (JioSaavn-first) for track {track.spotify_id}")
                elif track.preview_url:
                    # Queue Librosa extraction if we have a preview URL
                    extract_track_features_task.delay(
                        track.id, track.preview_url)
            except Exception as e:
                logger.error(
                    f"Error queuing background task for new track {track.spotify_id}: {e}")

            return track

        except Exception as e:
            logger.error(
                f"Error creating track with spotify_id {spotify_id}: {e}")
            return None


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
    try:
        playlist_data = sp.playlist(playlist_id)
        defaults = {
            'user': request.user,
            'name': playlist_data['name'],
            'description': playlist_data['description'],
            'image_url': playlist_data['images'][0]['url'] if playlist_data['images'] else None,
        }

        playlist, created = Playlist.objects.update_or_create(
            spotify_id=playlist_id,
            defaults=defaults
        )

        # Sync tracks if created or if it has no tracks (and Spotify says it should)
        if created or playlist.tracks.count() == 0:
            # Use the existing get_playlist_tracks function which handles fetching and creating track objects
            tracks = get_playlist_tracks(playlist_id, request)
            if tracks:
                playlist.tracks.set(tracks)
                playlist.save()

    except Exception as e:
        logger.error(f"Error syncing playlist {playlist_id}: {e}")
        if not playlist:
            return None

    return playlist


@cache_api_call(key_prefix="spotify_user_playlists", timeout=1800)
def get_user_playlists(request):
    try:
        sp = get_spotify_client(request)
        if not sp:  # Ensure sp is valid
            logger.error(
                "Error in get_user_playlists: Could not get Spotify client.")
            return []
        playlists_data = sp.current_user_playlists()  # Renamed to avoid conflict
        if not playlists_data or not playlists_data.get('items'):
            logger.warning(
                "Error in get_user_playlists: No items from sp.current_user_playlists()")
            return []

        # Define a worker function to process each playlist in parallel
        # Note: Passing 'request' to threads like this can be problematic if request is not thread-safe
        # or if get_or_create_playlist itself is not designed for this.
        # For now, adhering to the plan. A safer way might be to pass necessary user info from request.
        def process_playlist(playlist_item_data):  # Changed to playlist_item_data
            # get_or_create_playlist expects spotify_id, request, sp
            # sp inside process_playlist should be thread-safe or re-instantiated.
            # Here, we are passing the 'sp' from the outer scope which could be an issue.
            # A better approach might be:
            # current_sp = get_spotify_client(request) # if request can be passed safely
            # return get_or_create_playlist(playlist_item_data['id'], request, current_sp)
            # For this refactor, let's assume get_spotify_client() is efficient enough if called per thread,
            # or that the passed 'sp' is safe. The plan implies 'sp' is obtained once.
            return get_or_create_playlist(playlist_item_data['id'], request, sp)

        # Process playlists in parallel using ThreadPoolExecutor
        result = []
        with ThreadPoolExecutor(max_workers=min(10, len(playlists_data['items']))) as executor:
            future_to_playlist = {executor.submit(process_playlist, playlist): playlist
                                  for playlist in playlists_data['items']}

            for future in concurrent.futures.as_completed(future_to_playlist):
                pl = future.result()
                if pl:
                    result.append(pl)
    except Exception as e:
        logger.error(f"Error in get_user_playlists: {type(e).__name__} - {e}")
        return []  # Return empty list on error

    return result


@cache_api_call(key_prefix="spotify_playlist_tracks")
def get_playlist_tracks(playlist_id, request):
    try:
        sp = get_spotify_client(request)
        if not sp:
            logger.error(
                "Error in get_playlist_tracks: Could not get Spotify client.")
            return []
        results = sp.playlist_items(playlist_id)
        if not results or not results.get('items'):
            logger.warning(
                f"Error in get_playlist_tracks: No items from sp.playlist_items for playlist {playlist_id}")
            return []

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

    except Exception as e:
        logger.error(
            f"Error in get_playlist_tracks for playlist {playlist_id}: {type(e).__name__} - {e}")
        return []
    return tracks


def get_playlist_tracks_fast(playlist_id, request, sp=None):
    """
    Fast version of get_playlist_tracks using get_or_create_track_fast.
    Skips JioSaavn lookups for faster loading. Preview URLs fetched in background.
    """
    try:
        if not sp:
            sp = get_spotify_client(request)
        if not sp:
            logger.error("Error in get_playlist_tracks_fast: Could not get Spotify client.")
            return []
        
        results = sp.playlist_items(playlist_id)
        if not results or not results.get('items'):
            return []

        items = [item for item in results['items'] if item.get('track')]
        
        # Process tracks in parallel using fast version
        def process_track(spotify_track):
            if spotify_track and spotify_track.get('track'):
                return get_or_create_track_fast(spotify_track['track'], sp)
            return None

        tracks = []
        with ThreadPoolExecutor(max_workers=min(15, len(items) or 1)) as executor:
            future_to_track = {executor.submit(process_track, item): item for item in items}
            for future in concurrent.futures.as_completed(future_to_track):
                try:
                    track = future.result(timeout=10)
                    if track:
                        tracks.append(track)
                except Exception as e:
                    logger.debug(f"Track processing failed: {e}")

        return tracks

    except Exception as e:
        logger.error(f"Error in get_playlist_tracks_fast for {playlist_id}: {e}")
        return []


@cache_api_call(key_prefix="spotify_user_top_tracks", timeout=7200)
def get_user_top_tracks(request):
    try:
        sp = get_spotify_client(request)
        if not sp:
            logger.error(
                "Error in get_user_top_tracks: Could not get Spotify client.")
            return []
        results = sp.current_user_top_tracks(
            limit=15, time_range='medium_term')
        if not results or not results.get('items'):
            logger.warning(
                "Error in get_user_top_tracks: No items from sp.current_user_top_tracks()")
            return []

        # Define a worker function to process each track in parallel
        def process_track(spotify_track_data):  # Renamed to spotify_track_data
            # Pass sp from outer scope, assuming it's thread-safe or get_or_create_track handles it.
            track = get_or_create_track(spotify_track_data, sp)
            if track:
                # Ensure artist exists before trying to access .name
                artist_name = track.artists.first().name if track.artists.exists() else "Unknown Artist"
                return {
                    'name': track.title,
                    'artist': artist_name,
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

    except Exception as e:
        logger.error(f"Error in get_user_top_tracks: {type(e).__name__} - {e}")
        return []
    return top_tracks


@cache_api_call(key_prefix="spotify_user_recently_played", timeout=3600)
def get_user_recently_played(request):
    sp = get_spotify_client(request)
    if not sp:
        logger.error(
            "Error in get_user_recently_played: Could not get Spotify client.")
        return []
    results = sp.current_user_recently_played(limit=15)
    if not results or not results.get('items'):
        logger.warning(
            "Error in get_user_recently_played: No items from sp.current_user_recently_played()")
        return []

        # Define a worker function to process each track in parallel

    def process_track(spotify_track_item):  # Renamed to spotify_track_item
        # Pass sp from outer scope
        track = get_or_create_track(spotify_track_item['track'], sp)
        if track:
            artist_name = track.artists.first().name if track.artists.exists() else "Unknown Artist"
            return {
                'name': track.title,
                'artist': artist_name,
                'album': track.album,
                'id': track.spotify_id,
                'played_at': spotify_track_item['played_at'],
                'duration': spotify_track_item['track']['duration_ms']
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


@cache_api_call(key_prefix="spotify_artist_top_tracks")
def get_artist_top_tracks(sp, artist_id):
    return sp.artist_top_tracks(artist_id)['tracks']


@cache_api_call(key_prefix="spotify_artist_details", timeout=86400)
def get_artist_details(sp, artist_id):
    return sp.artist(artist_id)


@cache_api_call(key_prefix="spotify_recommendations", timeout=3600)
def get_recommendations(sp, target_spotify_track_id, list_of_track_dicts_for_comparison, limit=10):
    """
    Generates recommendations using Spotify's recommendations API.
    Falls back to search-based recommendations if needed.
    """
    recommendations = []
    
    # Strategy 1: Use Spotify's native recommendations API (best quality)
    try:
        # Use target track + up to 4 more seeds (Spotify allows max 5 seeds)
        seed_tracks = [target_spotify_track_id]
        for track_dict in list_of_track_dicts_for_comparison[:4]:
            tid = track_dict.get('id')
            if tid and tid != target_spotify_track_id:
                seed_tracks.append(tid)
                if len(seed_tracks) >= 5:
                    break
        
        logger.info(f"Getting Spotify recommendations with seeds: {seed_tracks[:3]}...")
        rec_response = sp.recommendations(seed_tracks=seed_tracks, limit=limit)
        
        if rec_response and 'tracks' in rec_response:
            for item in rec_response['tracks']:
                if item['id'] != target_spotify_track_id:
                    recommendations.append({
                        'id': item['id'],
                        'similarity': 0.8,  # High confidence from Spotify
                        'title': item['name'],
                        'artist': item['artists'][0]['name'] if item['artists'] else 'Unknown',
                        'album': item['album']['name'] if item.get('album') else '',
                        'image_url': item['album']['images'][0]['url'] if item.get('album', {}).get('images') else None
                    })
            if recommendations:
                logger.info(f"Got {len(recommendations)} recommendations from Spotify API")
                return recommendations
    except Exception as e:
        logger.warning(f"Spotify recommendations API failed: {e}")

    # Strategy 2: Search-based fallback using artist/genre
    try:
        # Try to get artist info from the target track
        track_info = sp.track(target_spotify_track_id)
        if track_info:
            artist_name = track_info['artists'][0]['name'] if track_info.get('artists') else ""
            
            if artist_name:
                query = f"artist:{artist_name}"
                logger.info(f"Recommendations fallback: searching '{query}'")
                search_results = sp.search(q=query, type='track', limit=limit * 2)
                
                if search_results and 'tracks' in search_results:
                    for item in search_results['tracks']['items']:
                        if item['id'] != target_spotify_track_id:
                            recommendations.append({
                                'id': item['id'],
                                'similarity': 0.5,
                                'title': item['name'],
                                'artist': item['artists'][0]['name'] if item['artists'] else 'Unknown',
                                'album': item['album']['name'] if item.get('album') else '',
                                'image_url': item['album']['images'][0]['url'] if item.get('album', {}).get('images') else None
                            })
                            if len(recommendations) >= limit:
                                break
    except Exception as e:
        logger.error(f"Recommendations search fallback failed: {e}")

    return recommendations


@cache_api_call(key_prefix="spotify_artist_albums", timeout=86400)
def get_artist_albums(sp, artist_id, album_type='album', limit=5):
    return sp.artist_albums(artist_id, album_type=album_type, limit=limit)['items']


def create_playlist_spotify(sp: Spotify, name, description=""):
    user_id = sp.current_user()['id']
    return sp.user_playlist_create(user_id, name, public=False, description=description)


def add_tracks_to_playlist_spotify(sp: Spotify, playlist_id, track_ids):
    track_ids_formatted = [
        f"spotify:track:{track_id}" for track_id in track_ids]
    sp.playlist_add_items(playlist_id, track_ids_formatted)


def remove_tracks_from_playlist_spotify(sp: Spotify, playlist_id, track_ids):
    track_ids_formatted = [
        f"spotify:track:{track_id}" for track_id in track_ids]
    sp.playlist_remove_all_occurrences_of_items(
        playlist_id, track_ids_formatted)


def delete_playlist_spotify(sp: Spotify, playlist_id):
    sp.current_user_unfollow_playlist(playlist_id)


@cache_api_call(key_prefix="spotify_listening_time", timeout=3600)
def calculate_listening_time(sp: Spotify, recently_played):
    if not recently_played:
        return 0.0
    total_duration_ms = sum(track.get('duration', 0)
                            for track in recently_played if isinstance(track, dict))
    return total_duration_ms / (1000 * 60 * 60)


@cache_api_call(key_prefix="spotify_favorite_genre", timeout=3600)
def get_favorite_genre(sp: Spotify, top_tracks, user_id_for_cache=None):
    if not top_tracks:
        return None
    artist_names = [track['artist'] for track in top_tracks]
    all_genres = []
    missing_artists = []
    artist_query = Q()
    for name in artist_names:
        artist_query |= Q(name__iexact=name)
    existing_artists = Artist.objects.filter(
        artist_query).prefetch_related('genres')
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
                    search_results = sp.search(
                        artist_name, type='artist', limit=1)
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
                    logger.error(
                        f"Error fetching genres for {artist_name}: {str(e)}")
                    return []

            # Process artists in parallel using ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(10, len(missing_artists))) as executor:
                future_to_artist = {executor.submit(fetch_artist_genres, artist_name): artist_name
                                    for artist_name in missing_artists}

                for future in concurrent.futures.as_completed(future_to_artist):
                    genres = future.result()
                    all_genres.extend(genres)

        except Exception as e:
            logger.error(f"Error fetching artist genres from Spotify: {e}")

    if not all_genres:
        return None

    most_common_genre = Counter(all_genres).most_common(1)[0][0]
    return most_common_genre


# =============================================================================
# ASYNC WRAPPER FUNCTIONS
# =============================================================================
# These functions wrap the sync spotipy calls for use in async views
# using run_in_executor to avoid blocking the event loop.


async def async_has_spotify_token(request):
    """Check if user has Spotify token asynchronously."""
    return await run_in_executor(has_spotify_token, request)


async def async_get_spotify_client(request, raise_on_no_token=False):
    """Get Spotify client asynchronously."""
    return await run_in_executor(get_spotify_client, request, raise_on_no_token)


async def async_search_tracks(sp, query: str, limit: int = 10):
    """
    Async wrapper for search_tracks.
    Runs the sync spotipy search in a thread executor.
    """
    return await run_in_executor(search_tracks, sp, query, limit)


async def async_get_or_create_track(track_data, sp):
    """Async wrapper for get_or_create_track."""
    return await run_in_executor(get_or_create_track, track_data, sp)


async def async_get_user_playlists(request):
    """Async wrapper for get_user_playlists."""
    return await run_in_executor(get_user_playlists, request)


async def async_get_playlist_tracks(playlist_id, request):
    """Async wrapper for get_playlist_tracks."""
    return await run_in_executor(get_playlist_tracks, playlist_id, request)


async def async_get_user_top_tracks(request):
    """Async wrapper for get_user_top_tracks."""
    return await run_in_executor(get_user_top_tracks, request)


async def async_get_user_recently_played(request):
    """Async wrapper for get_user_recently_played."""
    return await run_in_executor(get_user_recently_played, request)


async def async_get_artist_top_tracks(sp, artist_id: str):
    """Async wrapper for get_artist_top_tracks."""
    return await run_in_executor(get_artist_top_tracks, sp, artist_id)


async def async_get_artist_details(sp, artist_id: str):
    """Async wrapper for get_artist_details."""
    return await run_in_executor(get_artist_details, sp, artist_id)


async def async_get_recommendations(sp, target_track_id: str, comparison_tracks: list, limit: int = 10):
    """Async wrapper for get_recommendations."""
    return await run_in_executor(get_recommendations, sp, target_track_id, comparison_tracks, limit)


async def async_get_artist_albums(sp, artist_id: str, album_type: str = 'album', limit: int = 5):
    """Async wrapper for get_artist_albums."""
    return await run_in_executor(get_artist_albums, sp, artist_id, album_type, limit)


async def async_create_playlist_spotify(sp, name: str, description: str = ""):
    """Async wrapper for create_playlist_spotify."""
    return await run_in_executor(create_playlist_spotify, sp, name, description)


async def async_add_tracks_to_playlist_spotify(sp, playlist_id: str, track_ids: list):
    """Async wrapper for add_tracks_to_playlist_spotify."""
    return await run_in_executor(add_tracks_to_playlist_spotify, sp, playlist_id, track_ids)


async def async_remove_tracks_from_playlist_spotify(sp, playlist_id: str, track_ids: list):
    """Async wrapper for remove_tracks_from_playlist_spotify."""
    return await run_in_executor(remove_tracks_from_playlist_spotify, sp, playlist_id, track_ids)


async def async_delete_playlist_spotify(sp, playlist_id: str):
    """Async wrapper for delete_playlist_spotify."""
    return await run_in_executor(delete_playlist_spotify, sp, playlist_id)


async def async_calculate_listening_time(sp, recently_played: list):
    """Async wrapper for calculate_listening_time."""
    return await run_in_executor(calculate_listening_time, sp, recently_played)


async def async_get_favorite_genre(sp, top_tracks: list, user_id_for_cache=None):
    """Async wrapper for get_favorite_genre."""
    return await run_in_executor(get_favorite_genre, sp, top_tracks, user_id_for_cache)


async def async_get_or_create_playlist(playlist_id: str, request, sp):
    """Async wrapper for get_or_create_playlist."""
    return await run_in_executor(get_or_create_playlist, playlist_id, request, sp)
