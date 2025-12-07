"""
JioSaavn API Client - Async Implementation

Provides async methods to interact with the JioSaavn API for:
- Song search and details
- Album search and details  
- Artist search and details
- Song suggestions

Uses httpx for async HTTP requests with caching support.
"""
from services.utils import cache_api_call
import logging
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

import httpx

from services.async_utils import async_cache_api_call, run_in_executor

logger = logging.getLogger(__name__)

BASE_URL = "https://saavn.sumit.co/api"

# HTTP client timeout settings - optimized for fast response
REQUEST_TIMEOUT = 5.0  # Reduced from 10s for faster fallback
MAX_RETRIES = 2  # Reduced from 3 for faster fallback


# =============================================================================
# ASYNC HTTP CLIENT
# =============================================================================

async def _make_request_async(
    endpoint: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = REQUEST_TIMEOUT
) -> Optional[Dict[str, Any]]:
    """
    Async helper function to make GET requests to the JioSaavn API.

    Args:
        endpoint: API endpoint path
        params: Optional query parameters
        timeout: Request timeout in seconds

    Returns:
        JSON response data or None on error
    """
    if params is None:
        params = {}

    url = f"{BASE_URL}{endpoint}"

    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()

                json_response = response.json()

                if json_response.get('success'):
                    return json_response.get('data')

                logger.warning(
                    f"JioSaavn API request to {endpoint} returned success=False: {json_response}"
                )
                return None

        except httpx.HTTPStatusError as e:
            logger.error(
                f"HTTP error for {endpoint} (attempt {attempt + 1}): {e}")
            if attempt == MAX_RETRIES - 1:
                return None
        except httpx.RequestError as e:
            logger.error(
                f"Request error for {endpoint} (attempt {attempt + 1}): {e}")
            if attempt == MAX_RETRIES - 1:
                return None
        except ValueError as json_err:
            logger.error(f"JSON decoding error for {endpoint}: {json_err}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error for {endpoint}: {e}")
            return None

    return None


def _make_request(endpoint: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """
    Sync helper function to make GET requests to the JioSaavn API.
    Maintained for backwards compatibility.
    """
    import requests

    if params is None:
        params = {}

    try:
        response = requests.get(
            f"{BASE_URL}{endpoint}", params=params, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()

        json_response = response.json()

        if json_response.get('success'):
            return json_response.get('data')

        logger.warning(
            f"JioSaavn API request to {endpoint} returned success=False: {json_response}"
        )
        return None

    except requests.exceptions.RequestException as req_err:
        logger.error(f"Request exception for {endpoint}: {req_err}")
    except ValueError as json_err:
        logger.error(f"JSON decoding error for {endpoint}: {json_err}")
    except Exception as e:
        logger.error(f"Unexpected error for {endpoint}: {e}")

    return None


# =============================================================================
# DATA STANDARDIZATION HELPERS
# =============================================================================

def _get_image_url(images_field: Any) -> Optional[str]:
    """Extract best quality image URL from images field."""
    if isinstance(images_field, list) and images_field:
        # Prefer 500x500, then 150x150
        for img in images_field:
            if isinstance(img, dict) and img.get('quality') == '500x500':
                return img.get('url')
        for img in images_field:
            if isinstance(img, dict) and img.get('quality') == '150x150':
                return img.get('url')
        if images_field[0]:
            return images_field[0].get('url') if isinstance(images_field[0], dict) else None
    return None


def _get_preview_url(download_urls_field: Any) -> Optional[str]:
    """Extract best quality preview/download URL."""
    if isinstance(download_urls_field, list) and download_urls_field:
        # Prefer 320kbps, then 160kbps
        for dl_option in reversed(download_urls_field):
            if isinstance(dl_option, dict) and dl_option.get('quality') == '320kbps':
                return dl_option.get('url')
        for dl_option in reversed(download_urls_field):
            if isinstance(dl_option, dict) and dl_option.get('quality') == '160kbps':
                return dl_option.get('url')
        if download_urls_field[-1]:
            return download_urls_field[-1].get('url') if isinstance(download_urls_field[-1], dict) else None
    return None


def _standardize_song_data(song_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Standardizes song data from JioSaavn API response."""
    if not song_data or not isinstance(song_data, dict):
        return None

    # Artist extraction
    artists_list = []
    artists_data = song_data.get('artists', {})
    if isinstance(artists_data, dict):
        primary = artists_data.get('primary', [])
        if isinstance(primary, list):
            artists_list = [a.get('name') for a in primary if a.get('name')]
    elif isinstance(artists_data, list):
        artists_list = [a.get('name') for a in artists_data if a.get('name')]

    album_data = song_data.get('album', {})
    album_name = album_data.get('name') if isinstance(
        album_data, dict) else album_data

    return {
        'id': song_data.get('id'),
        'title': song_data.get('name') or song_data.get('title'),
        'artists': artists_list,
        'album': album_name,
        'image_url': _get_image_url(song_data.get('image')),
        'preview_url': _get_preview_url(song_data.get('downloadUrl')),
        'duration': int(song_data.get('duration', 0)) if song_data.get('duration') else 0,
        'release_date': song_data.get('year') or song_data.get('releaseDate'),
        'language': song_data.get('language'),
        'source': 'JioSaavn'
    }


def _normalize_text(text: str) -> str:
    """Normalize text for comparison by lowercasing and removing extra spaces."""
    if not text:
        return ""
    return " ".join(text.lower().split())


def _calculate_similarity(text1: str, text2: str) -> float:
    """Calculate similarity ratio between two strings."""
    return SequenceMatcher(None, _normalize_text(text1), _normalize_text(text2)).ratio()


def find_best_matching_song(
    search_results: List[Dict[str, Any]],
    target_title: str,
    target_artists: List[str],
    min_artist_similarity: float = 0.5,
    min_title_similarity: float = 0.5
) -> Optional[Dict[str, Any]]:
    """
    Find the best matching song from search results based on title and artist similarity.

    Args:
        search_results: List of standardized song dictionaries from JioSaavn
        target_title: Expected song title
        target_artists: List of expected artist names
        min_artist_similarity: Minimum similarity ratio for artist matching (0-1)
        min_title_similarity: Minimum similarity ratio for title matching (0-1)

    Returns:
        Best matching song dictionary or None if no good match found
    """
    if not search_results:
        return None

    # Normalize target artists
    target_artist_names = [_normalize_text(a) for a in target_artists if a]
    target_artist_str = " ".join(target_artist_names)
    best_match = None
    best_score = 0.0

    for song in search_results:
        if not song:
            continue

        # Calculate title similarity
        song_title = song.get('title', '')
        title_sim = _calculate_similarity(target_title, song_title)
        
        # Skip if title similarity is too low - require at least 0.5 to avoid wrong songs
        if title_sim < max(min_title_similarity, 0.5):
            logger.debug(
                f"Skipping '{song_title}' - title similarity {title_sim:.2f} < {max(min_title_similarity, 0.5)}"
            )
            continue

        # Calculate artist similarity
        song_artists = song.get('artists', [])
        song_artist_names = [_normalize_text(a) for a in song_artists if a]
        song_artist_str = " ".join(song_artist_names)

        # Check if any target artist matches any song artist (partial match)
        artist_sim = 0.0
        if target_artist_names and song_artist_names:
            # Check direct string similarity
            artist_sim = _calculate_similarity(
                target_artist_str, song_artist_str)

            # Also check if any target artist is contained in any song artist or vice versa
            for t_artist in target_artist_names:
                for s_artist in song_artist_names:
                    if t_artist in s_artist or s_artist in t_artist:
                        # Boost for substring match
                        artist_sim = max(artist_sim, 0.7)
                    partial_sim = _calculate_similarity(t_artist, s_artist)
                    if partial_sim > 0.8:  # Very high similarity for one artist
                        artist_sim = max(artist_sim, partial_sim)

        # Skip if artist similarity is too low (likely wrong song)
        if target_artists and artist_sim < min_artist_similarity:
            logger.debug(
                f"Skipping '{song_title}' by {song_artists} - "
                f"artist similarity {artist_sim:.2f} < {min_artist_similarity}"
            )
            continue

        # Combined score: title-weighted average (title matters most to avoid wrong songs)
        # Title threshold 0.4 is too low for correct matching, require higher effective score
        combined_score = (title_sim * 0.6) + (artist_sim * 0.4)
        
        # Require minimum combined score to avoid false positives
        if combined_score < 0.55:
            logger.debug(
                f"Skipping '{song_title}' - combined score {combined_score:.2f} < 0.55"
            )
            continue

        logger.debug(
            f"Song: '{song_title}' by {song_artists} - "
            f"title_sim={title_sim:.2f}, artist_sim={artist_sim:.2f}, combined={combined_score:.2f}"
        )

        if combined_score > best_score:
            best_score = combined_score
            best_match = song

    if best_match:
        logger.info(
            f"Found best match: '{best_match.get('title')}' by {best_match.get('artists')} "
            f"with score {best_score:.2f}"
        )
    else:
        logger.warning(
            f"No suitable match found for '{target_title}' by {target_artists}"
        )

    return best_match


async def find_best_matching_song_async(
    query: str,
    target_title: str,
    target_artists: List[str],
    min_artist_similarity: float = 0.5,
    target_duration_ms: int = 0
) -> Optional[Dict[str, Any]]:
    """
    Async version: Search JioSaavn and find the best matching song.
    Uses strict matching including duration validation.

    Args:
        query: Search query string
        target_title: Expected song title
        target_artists: List of expected artist names
        min_artist_similarity: Minimum similarity ratio for artist matching (0-1)
        target_duration_ms: Expected duration in milliseconds (for validation)

    Returns:
        Best matching song with details or None
    """
    search_results = await search_songs_async(query, limit=5)
    
    if not search_results:
        return None
    
    # Find best match with duration validation
    best_match = None
    best_score = 0.0
    
    target_title_lower = target_title.lower()
    target_artist_names = [a.lower() for a in target_artists if a]
    
    for song in search_results:
        if not song or not song.get('preview_url'):
            continue
        
        song_title = song.get('title', '').lower()
        song_artist_list = [a.lower() for a in song.get('artists', [])]
        song_artists_str = ' '.join(song_artist_list)
        song_duration = song.get('duration', 0)  # in seconds
        
        # Title similarity check - require minimum 0.5 for async (stricter)
        title_sim = _calculate_similarity(target_title_lower, song_title)
        if title_sim < 0.5:  # Skip if title too different
            logger.debug(f"Skipping '{song_title}' - title similarity {title_sim:.2f} < 0.5")
            continue
        
        # Artist similarity check
        artist_sim = 0.0
        for target_artist in target_artist_names:
            if target_artist in song_artists_str:
                artist_sim = max(artist_sim, 0.8)
            for song_artist in song_artist_list:
                partial_sim = _calculate_similarity(target_artist, song_artist)
                if partial_sim > artist_sim:
                    artist_sim = partial_sim
        
        if target_artists and artist_sim < min_artist_similarity:
            logger.debug(f"Skipping '{song_title}' - artist similarity {artist_sim:.2f} < {min_artist_similarity}")
            continue
        
        # Duration validation (within 15 seconds)
        if target_duration_ms > 0 and song_duration > 0:
            duration_diff = abs(song_duration * 1000 - target_duration_ms)
            if duration_diff > 15000:  # More than 15 seconds difference
                logger.debug(f"Skipping '{song_title}' - duration mismatch: {song_duration}s vs {target_duration_ms/1000}s")
                continue
        
        # Calculate combined score (title-weighted to avoid false positives)
        score = (title_sim * 0.6) + (artist_sim * 0.4)
        
        # Require minimum combined score
        if score < 0.55:
            logger.debug(f"Skipping '{song_title}' - combined score {score:.2f} < 0.55")
            continue
            
        if score > best_score:
            best_score = score
            best_match = song
    
    if best_match:
        logger.info(f"JioSaavn matched: '{best_match.get('title')}' by {best_match.get('artists')} (score: {best_score:.2f})")
    
    return best_match


def _standardize_album_data(album_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Standardizes album data from JioSaavn API response."""
    if not album_data or not isinstance(album_data, dict):
        return None

    artists_list = []
    artists_data = album_data.get('artists', {})
    if isinstance(artists_data, dict):
        primary_artists = artists_data.get('primary', [])
        if isinstance(primary_artists, list):
            artists_list = [artist.get(
                'name') for artist in primary_artists if artist.get('name')]

    return {
        'id': album_data.get('id'),
        'name': album_data.get('name'),
        'description': album_data.get('description'),
        'year': album_data.get('year'),
        'artists': artists_list,
        'image_url': _get_image_url(album_data.get('image')),
        'song_count': album_data.get('songCount'),
        'language': album_data.get('language'),
        'source': 'JioSaavn'
    }


def _standardize_artist_data(artist_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Standardizes artist data from JioSaavn API response."""
    if not artist_data or not isinstance(artist_data, dict):
        return None

    return {
        'id': artist_data.get('id'),
        'name': artist_data.get('name'),
        'role': artist_data.get('role'),
        'type': artist_data.get('type'),
        'image_url': _get_image_url(artist_data.get('image')),
        'source': 'JioSaavn'
    }


# =============================================================================
# ASYNC API METHODS
# =============================================================================

@async_cache_api_call(key_prefix="jiosaavn_search_songs_async", timeout=900, skip_first_arg=False)
async def search_songs_async(query: str, page: int = 1, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Async search for songs on JioSaavn.

    Args:
        query: Search query string
        page: Page number (default: 1)
        limit: Results per page (default: 10)

    Returns:
        List of standardized song dictionaries
    """
    endpoint = "/search/songs"
    params = {'query': query, 'page': page, 'limit': limit}
    data = await _make_request_async(endpoint, params)

    if data and 'results' in data and isinstance(data['results'], list):
        standardized_results = []
        for song_data in data['results']:
            std_song = _standardize_song_data(song_data)
            if std_song:
                standardized_results.append(std_song)
        return standardized_results

    logger.warning(f"JioSaavn song search for '{query}' returned no results")
    return []


@async_cache_api_call(key_prefix="jiosaavn_song_details_async", timeout=86400, skip_first_arg=False)
async def get_song_details_async(song_id: str) -> Optional[Dict[str, Any]]:
    """
    Async fetch details for a specific song ID from JioSaavn.

    Args:
        song_id: JioSaavn song ID

    Returns:
        Standardized song dictionary or None
    """
    logger.info(f"Fetching JioSaavn song details for id: {song_id}")
    endpoint = f"/songs/{song_id}"
    data = await _make_request_async(endpoint)

    if data:
        song_data = data
        if isinstance(data, list) and data:
            song_data = data[0]
        elif isinstance(data, dict) and 'results' in data:
            if data['results']:
                song_data = data['results'][0]

        return _standardize_song_data(song_data)

    logger.warning(
        f"JioSaavn song details for ID '{song_id}' returned no result")
    return None


@async_cache_api_call(key_prefix="jiosaavn_search_albums_async", timeout=900, skip_first_arg=False)
async def search_albums_async(query: str, page: int = 0, limit: int = 10) -> List[Dict[str, Any]]:
    """Async search for albums on JioSaavn."""
    endpoint = "/search/albums"
    params = {'query': query, 'page': page, 'limit': limit}
    data = await _make_request_async(endpoint, params)

    if data and 'results' in data and isinstance(data['results'], list):
        return [_standardize_album_data(a) for a in data['results'] if _standardize_album_data(a)]

    logger.warning(f"JioSaavn album search for '{query}' returned no results")
    return []


@async_cache_api_call(key_prefix="jiosaavn_search_artists_async", timeout=900, skip_first_arg=False)
async def search_artists_async(query: str, page: int = 0, limit: int = 10) -> List[Dict[str, Any]]:
    """Async search for artists on JioSaavn."""
    endpoint = "/search/artists"
    params = {'query': query, 'page': page, 'limit': limit}
    data = await _make_request_async(endpoint, params)

    if data and 'results' in data and isinstance(data['results'], list):
        return [_standardize_artist_data(a) for a in data['results'] if _standardize_artist_data(a)]

    logger.warning(f"JioSaavn artist search for '{query}' returned no results")
    return []


@async_cache_api_call(key_prefix="jiosaavn_album_details_async", timeout=86400, skip_first_arg=False)
async def get_album_details_async(album_id: str) -> Optional[Dict[str, Any]]:
    """Async fetch album details from JioSaavn."""
    logger.info(f"Fetching JioSaavn album details for id: {album_id}")
    endpoint = "/albums"
    params = {'id': album_id}
    data = await _make_request_async(endpoint, params)

    if data:
        std_album = _standardize_album_data(data)
        if std_album:
            songs = data.get('songs', [])
            if isinstance(songs, list):
                std_album['songs'] = [
                    _standardize_song_data(s) for s in songs if _standardize_song_data(s)
                ]
            return std_album

    logger.warning(
        f"JioSaavn album details for ID '{album_id}' returned no result")
    return None


@async_cache_api_call(key_prefix="jiosaavn_artist_details_async", timeout=86400, skip_first_arg=False)
async def get_artist_details_async(
    artist_id: str,
    page: int = 0,
    song_count: int = 10,
    album_count: int = 10,
    sort_by: str = 'popularity',
    sort_order: str = 'desc'
) -> Optional[Dict[str, Any]]:
    """Async fetch artist details from JioSaavn."""
    logger.info(f"Fetching JioSaavn artist details for id: {artist_id}")
    endpoint = f"/artists/{artist_id}"
    params = {
        'page': page,
        'songCount': song_count,
        'albumCount': album_count,
        'sortBy': sort_by,
        'sortOrder': sort_order
    }
    data = await _make_request_async(endpoint, params)

    if data:
        std_artist = {
            'id': data.get('id'),
            'name': data.get('name'),
            'type': data.get('type'),
            'image_url': _get_image_url(data.get('image')),
            'follower_count': data.get('followerCount'),
            'fan_count': data.get('fanCount'),
            'is_verified': data.get('isVerified'),
            'dominant_language': data.get('dominantLanguage'),
            'dominant_type': data.get('dominantType'),
            'bio': data.get('bio'),
            'dob': data.get('dob'),
            'fb': data.get('fb'),
            'twitter': data.get('twitter'),
            'wiki': data.get('wiki'),
            'source': 'JioSaavn'
        }

        # Add top songs
        top_songs = data.get('topSongs', [])
        if isinstance(top_songs, list):
            std_artist['top_songs'] = [
                _standardize_song_data(s) for s in top_songs if _standardize_song_data(s)
            ]

        # Add top albums
        top_albums = data.get('topAlbums', [])
        if isinstance(top_albums, list):
            std_artist['top_albums'] = [
                _standardize_album_data(a) for a in top_albums if _standardize_album_data(a)
            ]

        # Add singles
        singles = data.get('singles', [])
        if isinstance(singles, list):
            std_artist['singles'] = [
                _standardize_song_data(s) for s in singles if _standardize_song_data(s)
            ]

        return std_artist

    logger.warning(
        f"JioSaavn artist details for ID '{artist_id}' returned no result")
    return None


@async_cache_api_call(key_prefix="jiosaavn_song_suggestions_async", timeout=3600, skip_first_arg=False)
async def get_song_suggestions_async(song_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Async get song suggestions based on a song ID."""
    logger.info(f"Fetching JioSaavn song suggestions for id: {song_id}")
    endpoint = f"/songs/{song_id}/suggestions"
    params = {'limit': limit}
    data = await _make_request_async(endpoint, params)

    if data and isinstance(data, list):
        return [_standardize_song_data(s) for s in data if _standardize_song_data(s)]

    logger.warning(
        f"JioSaavn song suggestions for ID '{song_id}' returned no results")
    return []


# =============================================================================
# SYNC API METHODS (Backwards Compatibility)
# =============================================================================


@cache_api_call(key_prefix="jiosaavn_search_songs_v2", timeout=900, skip_first_arg=False)
def search_songs(query: str, page: int = 1, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Sync search for songs on JioSaavn.
    For backwards compatibility - use search_songs_async for async views.
    """
    endpoint = "/search/songs"
    params = {'query': query, 'page': page, 'limit': limit}
    data = _make_request(endpoint, params)

    if data and 'results' in data and isinstance(data['results'], list):
        return [_standardize_song_data(s) for s in data['results'] if _standardize_song_data(s)]

    logger.warning(f"JioSaavn song search for '{query}' returned no results")
    return []


@cache_api_call(key_prefix="jiosaavn_song_details_id", timeout=86400, skip_first_arg=False)
def get_song_details(song_id: str) -> Optional[Dict[str, Any]]:
    """
    Sync fetch song details from JioSaavn.
    For backwards compatibility - use get_song_details_async for async views.
    """
    logger.info(f"Fetching JioSaavn song details for id: {song_id}")
    endpoint = f"/songs/{song_id}"
    data = _make_request(endpoint)

    if data:
        song_data = data
        if isinstance(data, list) and data:
            song_data = data[0]
        elif isinstance(data, dict) and 'results' in data:
            if data['results']:
                song_data = data['results'][0]
        return _standardize_song_data(song_data)

    return None


@cache_api_call(key_prefix="jiosaavn_search_albums", timeout=900, skip_first_arg=False)
def search_albums(query: str, page: int = 0, limit: int = 10) -> List[Dict[str, Any]]:
    """Sync search for albums on JioSaavn."""
    endpoint = "/search/albums"
    params = {'query': query, 'page': page, 'limit': limit}
    data = _make_request(endpoint, params)

    if data and 'results' in data and isinstance(data['results'], list):
        return [_standardize_album_data(a) for a in data['results'] if _standardize_album_data(a)]

    logger.warning(f"JioSaavn album search for '{query}' returned no results")
    return []


@cache_api_call(key_prefix="jiosaavn_search_artists", timeout=900, skip_first_arg=False)
def search_artists(query: str, page: int = 0, limit: int = 10) -> List[Dict[str, Any]]:
    """Sync search for artists on JioSaavn."""
    endpoint = "/search/artists"
    params = {'query': query, 'page': page, 'limit': limit}
    data = _make_request(endpoint, params)

    if data and 'results' in data and isinstance(data['results'], list):
        return [_standardize_artist_data(a) for a in data['results'] if _standardize_artist_data(a)]

    logger.warning(f"JioSaavn artist search for '{query}' returned no results")
    return []


@cache_api_call(key_prefix="jiosaavn_album_details_id", timeout=86400, skip_first_arg=False)
def get_album_details(album_id: str) -> Optional[Dict[str, Any]]:
    """Sync fetch album details from JioSaavn."""
    logger.info(f"Fetching JioSaavn album details for id: {album_id}")
    endpoint = "/albums"
    params = {'id': album_id}
    data = _make_request(endpoint, params)

    if data:
        std_album = _standardize_album_data(data)
        if std_album:
            songs = data.get('songs', [])
            if isinstance(songs, list):
                std_album['songs'] = [_standardize_song_data(
                    s) for s in songs if _standardize_song_data(s)]
            return std_album

    logger.warning(
        f"JioSaavn album details for ID '{album_id}' returned no result")
    return None


@cache_api_call(key_prefix="jiosaavn_artist_details_id", timeout=86400, skip_first_arg=False)
def get_artist_details(
    artist_id: str,
    page: int = 0,
    song_count: int = 10,
    album_count: int = 10,
    sort_by: str = 'popularity',
    sort_order: str = 'desc'
) -> Optional[Dict[str, Any]]:
    """Sync fetch artist details from JioSaavn."""
    logger.info(f"Fetching JioSaavn artist details for id: {artist_id}")
    endpoint = f"/artists/{artist_id}"
    params = {
        'page': page,
        'songCount': song_count,
        'albumCount': album_count,
        'sortBy': sort_by,
        'sortOrder': sort_order
    }
    data = _make_request(endpoint, params)

    if data:
        std_artist = {
            'id': data.get('id'),
            'name': data.get('name'),
            'type': data.get('type'),
            'image_url': _get_image_url(data.get('image')),
            'follower_count': data.get('followerCount'),
            'fan_count': data.get('fanCount'),
            'is_verified': data.get('isVerified'),
            'dominant_language': data.get('dominantLanguage'),
            'dominant_type': data.get('dominantType'),
            'bio': data.get('bio'),
            'dob': data.get('dob'),
            'fb': data.get('fb'),
            'twitter': data.get('twitter'),
            'wiki': data.get('wiki'),
            'source': 'JioSaavn'
        }

        top_songs = data.get('topSongs', [])
        if isinstance(top_songs, list):
            std_artist['top_songs'] = [_standardize_song_data(
                s) for s in top_songs if _standardize_song_data(s)]

        top_albums = data.get('topAlbums', [])
        if isinstance(top_albums, list):
            std_artist['top_albums'] = [_standardize_album_data(
                a) for a in top_albums if _standardize_album_data(a)]

        singles = data.get('singles', [])
        if isinstance(singles, list):
            std_artist['singles'] = [_standardize_song_data(
                s) for s in singles if _standardize_song_data(s)]

        return std_artist

    logger.warning(
        f"JioSaavn artist details for ID '{artist_id}' returned no result")
    return None


@cache_api_call(key_prefix="jiosaavn_song_suggestions", timeout=3600, skip_first_arg=False)
def get_song_suggestions(song_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Sync get song suggestions based on a song ID."""
    logger.info(f"Fetching JioSaavn song suggestions for id: {song_id}")
    endpoint = f"/songs/{song_id}/suggestions"
    params = {'limit': limit}
    data = _make_request(endpoint, params)

    if data and isinstance(data, list):
        return [_standardize_song_data(s) for s in data if _standardize_song_data(s)]

    logger.warning(
        f"JioSaavn song suggestions for ID '{song_id}' returned no results")
    return []


@cache_api_call(key_prefix="jiosaavn_album_by_link", timeout=86400, skip_first_arg=False)
def get_album_by_link(link: str) -> Optional[Dict[str, Any]]:
    """Sync fetch album by JioSaavn link."""
    endpoint = "/albums"
    params = {'link': link}
    data = _make_request(endpoint, params)

    if data:
        std_album = _standardize_album_data(data)
        if std_album:
            songs = data.get('songs', [])
            if isinstance(songs, list):
                std_album['songs'] = [_standardize_song_data(
                    s) for s in songs if _standardize_song_data(s)]
            return std_album

    return None


@cache_api_call(key_prefix="jiosaavn_artist_by_link", timeout=86400, skip_first_arg=False)
def get_artist_by_link(
    link: str,
    page: int = 0,
    song_count: int = 10,
    album_count: int = 10,
    sort_by: str = 'popularity',
    sort_order: str = 'desc'
) -> Optional[Dict[str, Any]]:
    """Sync fetch artist by JioSaavn link."""
    endpoint = "/artists"
    params = {
        'link': link,
        'page': page,
        'songCount': song_count,
        'albumCount': album_count,
        'sortBy': sort_by,
        'sortOrder': sort_order
    }
    data = _make_request(endpoint, params)

    if data:
        return {
            'id': data.get('id'),
            'name': data.get('name'),
            'type': data.get('type'),
            'image_url': _get_image_url(data.get('image')),
            'follower_count': data.get('followerCount'),
            'fan_count': data.get('fanCount'),
            'is_verified': data.get('isVerified'),
            'dominant_language': data.get('dominantLanguage'),
            'dominant_type': data.get('dominantType'),
            'bio': data.get('bio'),
            'source': 'JioSaavn'
        }

    return None
