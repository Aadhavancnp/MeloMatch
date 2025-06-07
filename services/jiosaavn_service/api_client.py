import requests
import logging
# from django.core.cache import cache # Will be handled by decorator
from services.utils import cache_api_call # Import the new decorator

logger = logging.getLogger(__name__)

BASE_URL = "https://saavn.dev/api" # Using the saavn.dev proxy API

def _make_request(endpoint, params=None):
    """
    Helper function to make GET requests to the JioSaavn API.
    """
    if params is None:
        params = {}

    try:
        response = requests.get(f"{BASE_URL}{endpoint}", params=params, timeout=10)
        response.raise_for_status() # Raises an HTTPError for bad responses (4XX or 5XX)

        # The API seems to return data directly, sometimes in a 'data' key, sometimes not.
        # And sometimes results are in a 'results' key within 'data'.
        # We need to be flexible or standardize based on observed behavior for each endpoint.
        json_response = response.json()

        if isinstance(json_response, dict) and json_response.get('status') == 'SUCCESS':
            return json_response.get('data') # Often the actual data is nested here
        elif isinstance(json_response, list): # Some endpoints might return a list directly
             return json_response
        elif isinstance(json_response, dict) and 'results' in json_response: # For search that doesn't have a 'data' wrapper
            return json_response

        # If it's a dictionary but not 'SUCCESS' or no 'data' key, it might be an error or unexpected structure
        if isinstance(json_response, dict) and json_response.get('status') != 'SUCCESS':
            logger.error(f"JioSaavn API request to {endpoint} returned non-SUCCESS status: {json_response.get('message', 'No message')}")
            return None

        # If it's a dict and has 'data', but we didn't return it above, it implies success but unexpected data structure.
        # For now, let's assume if status is not explicitly FAILED, and data is there, it's usable.
        # This part might need refinement based on more API endpoint observations.
        if isinstance(json_response, dict) and 'data' in json_response:
             logger.warning(f"JioSaavn API response for {endpoint} had 'data' key but status was not 'SUCCESS'. Proceeding with data.")
             return json_response.get('data')

        logger.warning(f"JioSaavn API response for {endpoint} was not in expected format (status: SUCCESS with data key, or direct list/results dict). Response: {json_response}")
        return json_response # Return as is if structure is totally unknown but request didn't fail

    except requests.exceptions.HTTPError as http_err:
        logger.error(f"HTTP error occurred making request to JioSaavn API {endpoint}: {http_err} - Response: {response.text}")
    except requests.exceptions.RequestException as req_err:
        logger.error(f"Request exception occurred making request to JioSaavn API {endpoint}: {req_err}")
    except ValueError as json_err: # Includes JSONDecodeError
        logger.error(f"JSON decoding error for JioSaavn API {endpoint}: {json_err} - Response: {response.text if 'response' in locals() else 'N/A'}")
    except Exception as e:
        logger.error(f"An unexpected error occurred with JioSaavn API {endpoint}: {e}")

    return None


def _standardize_song_data(song_data):
    """
    Standardizes song data from JioSaavn API response.
    """
    if not song_data or not isinstance(song_data, dict):
        return None

    # Helper to safely extract image URLs, preferring higher quality
    def get_image_url(images_field):
        if isinstance(images_field, list) and images_field:
            # Prefer 500x500, then 150x150, then first available
            for img in images_field:
                if isinstance(img, dict) and img.get('quality') == '500x500': return img.get('link')
            for img in images_field:
                if isinstance(img, dict) and img.get('quality') == '150x150': return img.get('link')
            return images_field[0].get('link') # Fallback to the first image link
        elif isinstance(images_field, str): # Sometimes it's just a string URL
            return images_field
        return None

    # Helper to safely extract download URL (preview URL)
    def get_preview_url(download_urls_field):
        if isinstance(download_urls_field, list) and download_urls_field:
            # Prefer highest quality (last in list usually)
            for dl_option in reversed(download_urls_field):
                if isinstance(dl_option, dict) and dl_option.get('quality') == '320kbps': return dl_option.get('link')
            for dl_option in reversed(download_urls_field): # Fallback to 128kbps
                if isinstance(dl_option, dict) and dl_option.get('quality') == '128kbps': return dl_option.get('link')
            return download_urls_field[-1].get('link') # Fallback to last available link
        return None

    # Artist extraction can be complex due to various structures
    primary_artists_str = song_data.get('primaryArtists', "")
    if isinstance(primary_artists_str, list): # Sometimes it's a list of dicts
        artists_list = [a.get('name') for a in primary_artists_str if a.get('name')]
    elif isinstance(primary_artists_str, str):
        artists_list = [name.strip() for name in primary_artists_str.split(',')]
    else: # Fallback for other structures like 'more_info.artistMap.primary_artists'
        artist_map = song_data.get('moreInfo', {}).get('artistMap', {})
        primary_artist_list_of_dicts = artist_map.get('primary_artists', [])
        artists_list = [a.get('name') for a in primary_artist_list_of_dicts if a.get('name')]
        if not artists_list and song_data.get('artist'): # Very old structure
             artists_list = [song_data.get('artist')]


    return {
        'id': song_data.get('id'),
        'title': song_data.get('name') or song_data.get('title'), # API uses 'name' or 'title'
        'artists': artists_list if artists_list else [],
        'album': song_data.get('album', {}).get('name') or song_data.get('album'),
        'image_url': get_image_url(song_data.get('image')),
        'preview_url': get_preview_url(song_data.get('downloadUrl')),
        'duration': int(song_data.get('duration', 0)),
        'release_date': song_data.get('year') or song_data.get('releaseDate'),
        'language': song_data.get('language'),
        'source': 'JioSaavn'
    }

@cache_api_call(key_prefix="jiosaavn_search_songs", timeout=900) # Cache for 15 minutes
def search_songs(query, page=1, limit=10): # Decorator will use query, page, limit for key
    """
    Searches for songs on JioSaavn.
    """
    endpoint = "/search/songs"
    params = {'query': query, 'page': page, 'limit': limit}
    data = _make_request(endpoint, params) # _make_request handles actual HTTP and basic error checks

    if data and 'results' in data and isinstance(data['results'], list):
        standardized_results = []
        for song_data in data['results']:
            std_song = _standardize_song_data(song_data)
            if std_song:
                standardized_results.append(std_song)
        cache.set(cache_key, standardized_results, timeout=900) # Cache for 15 minutes
        return standardized_results
    else:
        logger.warning(f"JioSaavn song search for '{query}' returned no results or unexpected data structure: {data}")
        return []

def get_song_details(song_id):
    """
    Fetches details for a specific song ID from JioSaavn.
    The API endpoint seems to be /songs?id=<song_id> or /songs/<song_id>
    Based on observed patterns, /songs with id as param is common.
    """
    cache_key = f"jiosaavn_song_details_id_{song_id}"
    cached_result = cache.get(cache_key)
    if cached_result:
        logger.info(f"Returning cached JioSaavn song details for id: {song_id}")
        return cached_result

    logger.info(f"Fetching fresh JioSaavn song details for id: {song_id}")
    endpoint = "/songs" # Endpoint for fetching song details by ID
    params = {'id': song_id}
    data = _make_request(endpoint, params) # This returns a dict where key is song_id or list of songs

    if data:
        # API might return a list of songs or a dict with song_id as key
        song_data_list = data
        if isinstance(data, dict) and song_id in data: # If it's a dict keyed by song_id
            song_data_list = [data[song_id]]
        elif not isinstance(data, list): # If it's not a list and not the dict format above
            song_data_list = [] # Ensure it's a list for consistent processing below

        if song_data_list and isinstance(song_data_list, list) and len(song_data_list) > 0:
            std_song = _standardize_song_data(song_data_list[0])
            if std_song:
                cache.set(cache_key, std_song, timeout=86400) # Cache for 1 day
                return std_song
        else:
            logger.warning(f"JioSaavn song details for ID '{song_id}' returned no result or unexpected data: {data}")

    return None

# Placeholder functions
def search_albums(query, page=1, limit=10):
    logger.info(f"Placeholder: search_albums called with query '{query}'")
    return []

def search_artists(query, page=1, limit=10):
    logger.info(f"Placeholder: search_artists called with query '{query}'")
    return []

def get_album_details(album_id):
    logger.info(f"Placeholder: get_album_details called for album_id '{album_id}'")
    return None

def get_artist_details(artist_id):
    logger.info(f"Placeholder: get_artist_details called for artist_id '{artist_id}'")
    return None
