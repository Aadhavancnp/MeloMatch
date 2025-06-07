import os
import jwt
import datetime
import time
import applemusicpy
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

# Global variable to cache the client instance (simple in-memory cache)
# A more robust solution might use Django's cache framework for the token.
_apple_music_client = None
_apple_music_token = None
_apple_music_token_generated_time = None

def generate_developer_token():
    """
    Generates a developer token for Apple Music API.
    This function is specific to how apple-music-python might expect a token,
    or can be used if the library doesn't handle full JWT generation from raw key file.
    However, apple-music-python library usually handles this internally if key, secret, team_id are passed.
    Let's assume for now the library can take these directly. If not, this function would be needed.
    """
    global _apple_music_token, _apple_music_token_generated_time

    team_id = settings.APPLE_MUSIC_TEAM_ID
    key_id = settings.APPLE_MUSIC_KEY_ID
    private_key_path = settings.APPLE_MUSIC_PRIVATE_KEY_P8_FILE_PATH

    if not all([team_id, key_id, private_key_path]):
        logger.error("Apple Music API credentials (Team ID, Key ID, or Private Key Path) are not configured.")
        return None

    if not os.path.exists(private_key_path):
        logger.error(f"Apple Music private key file not found at: {private_key_path}")
        return None

    try:
        with open(private_key_path, 'r') as f:
            private_key = f.read()
    except Exception as e:
        logger.error(f"Error reading Apple Music private key file: {str(e)}")
        return None

    # Token usually valid for a few hours up to 6 months. Let's aim for 1 hour.
    # apple-music-python might prefer a longer expiry if it doesn't auto-refresh.
    # For now, let's generate a token valid for 1 hour.
    # The library itself might handle token generation and refreshing if we pass the components.

    # Check if existing token is still valid (e.g., within 50 minutes for a 1-hour token)
    if _apple_music_token and _apple_music_token_generated_time:
        if time.time() - _apple_music_token_generated_time < 3000: # 50 minutes
            logger.info("Using cached Apple Music developer token.")
            return _apple_music_token

    headers = {
        "alg": "ES256",
        "kid": key_id
    }
    payload = {
        "iss": team_id,
        "iat": int(datetime.datetime.now(tz=datetime.timezone.utc).timestamp()),
        "exp": int((datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(hours=1)).timestamp())
    }

    try:
        token = jwt.encode(payload, private_key, algorithm="ES256", headers=headers)
        _apple_music_token = token
        _apple_music_token_generated_time = time.time()
        logger.info("Successfully generated new Apple Music developer token.")
        return token
    except Exception as e:
        logger.error(f"Error generating Apple Music developer token: {str(e)}")
        return None


def get_apple_music_client():
    """
    Initializes and returns an Apple Music API client.
    Uses credentials from settings.py.
    Caches the client instance for reuse.
    """
    global _apple_music_client

    if _apple_music_client:
        # Here, we should also check if the token used by the client is still valid.
        # apple-music-python library handles token generation from secret, key_id, team_id directly.
        # So, re-initializing might be okay if it handles token expiry internally or if we pass a fresh token.
        # For simplicity, let's assume the library if given these params, manages the token.
        pass


    team_id = settings.APPLE_MUSIC_TEAM_ID
    key_id = settings.APPLE_MUSIC_KEY_ID
    # The library expects the actual private key content, not the file path.
    private_key_path = settings.APPLE_MUSIC_PRIVATE_KEY_P8_FILE_PATH

    if not all([team_id, key_id, private_key_path]):
        logger.error("Apple Music API credentials (Team ID, Key ID, or Private Key Path) are not configured.")
        return None

    if not os.path.exists(private_key_path):
        logger.error(f"Apple Music private key file not found at: {private_key_path}")
        return None

    try:
        with open(private_key_path, 'r') as f:
            private_key_content = f.read()
    except Exception as e:
        logger.error(f"Error reading Apple Music private key file: {str(e)}")
        return None

    try:
        # Initialize the client using apple-music-python library
        # The library expects: secret_key (private_key_content), key_id, team_id
        am = applemusicpy.AppleMusic(
            secret_key=private_key_content,
            key_id=key_id,
            team_id=team_id
        )
        # The library might make an initial request or just store credentials.
        # A simple test call could be added here to verify credentials if needed.
        # For example, fetching storefronts: am.storefronts()
        # However, to avoid unnecessary calls, we'll initialize and let subsequent calls fail if auth is wrong.

        _apple_music_client = am # Cache the client
        logger.info("Apple Music client initialized successfully.")
        return am
    except Exception as e:
        logger.error(f"Failed to initialize Apple Music client: {str(e)}")
        _apple_music_client = None # Clear cache on failure
        return None

def search_apple_music(query, limit=10, types=['songs', 'artists'], storefront='us'):
    """
    Searches Apple Music for tracks and artists.
    'types' can be a list containing 'songs', 'albums', 'artists'.
    Returns a dictionary with 'songs' and 'artists' lists.
    """
    client = get_apple_music_client()
    if not client:
        logger.warning("Apple Music client not available for search.")
        return {'songs': [], 'artists': []}

    results_songs = []
    results_artists = []

    try:
        # The apple-music-python library's search method takes 'types' as a string or list of strings.
        # It returns a dictionary where keys are the types requested.
        search_results = client.search(query, types=types, limit=limit, storefront=storefront)

        # Process songs
        if 'songs' in types and 'songs' in search_results and search_results['songs']['data']:
            for item in search_results['songs']['data']:
                attributes = item.get('attributes', {})
                artwork = attributes.get('artwork', {})
                # Construct artwork URL: replace {w} and {h} with desired dimensions
                artwork_url = artwork.get('url', '').replace('{w}', '300').replace('{h}', '300') if artwork.get('url') else None

                # Preview URL - usually in 'previews' array
                preview_url = None
                if attributes.get('previews'):
                    preview_url = attributes['previews'][0]['url']

                song_data = {
                    'id': item.get('id'),
                    'title': attributes.get('name'),
                    'artist': attributes.get('artistName'),
                    'album': attributes.get('albumName'),
                    'artwork_url': artwork_url,
                    'preview_url': preview_url,
                    'source': 'Apple Music'
                }
                results_songs.append(song_data)

        # Process artists
        if 'artists' in types and 'artists' in search_results and search_results['artists']['data']:
            for item in search_results['artists']['data']:
                attributes = item.get('attributes', {})
                # Artist artwork is not directly available in search results, needs separate fetch usually.
                # For now, we'll omit it from search results to keep it simple.
                artist_data = {
                    'id': item.get('id'),
                    'name': attributes.get('name'),
                    'genre': attributes.get('genreNames', [])[0] if attributes.get('genreNames') else None,
                    'source': 'Apple Music'
                }
                results_artists.append(artist_data)

        logger.info(f"Apple Music search for '{query}' returned {len(results_songs)} songs and {len(results_artists)} artists.")

    except Exception as e:
        logger.error(f"Error searching Apple Music for '{query}': {str(e)}")
        # This could be due to various reasons, including invalid token, network issues, API changes.
        # If token is invalid, get_apple_music_client() might need to handle regeneration more explicitly
        # or the library itself should.
        # For now, we just log and return empty.

    return {'songs': results_songs, 'artists': results_artists}
