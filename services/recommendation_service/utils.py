import numpy as np
import logging
from music.models import Track # Assuming Track model is needed if features are saved back
# from services.spotify_service.client import search_jiosaavn, download_preview, extract_audio_features, update_song_custom_audio_features
# The above line creates a circular dependency if _get_standardized_features_for_track calls them.
# These dependencies need careful management. For now, let's assume these are passed or handled.

logger = logging.getLogger(__name__)

# Standard feature keys for cosine similarity.
STANDARD_SIMILARITY_FEATURES = [
    'acousticness', 'danceability', 'energy', 'instrumentalness',
    'liveness', 'loudness', 'speechiness', 'tempo', 'valence', 'mode', 'key'
]

def _normalize_loudness(loudness_db):
    """Normalizes loudness from dB (e.g., -60 to 0) to a 0-1 scale."""
    if loudness_db is None: return 0.5 # Default if missing
    return max(0.0, min(1.0, (loudness_db + 60) / 60))

def _normalize_tempo(tempo_bpm):
    """Normalizes tempo (e.g., 50-250 BPM) to a 0-1 scale."""
    if tempo_bpm is None: return 0.5 # Default if missing
    return max(0.0, min(1.0, (tempo_bpm - 50) / 200))

def _get_standardized_features_for_track(track_obj, sp_client=None, spotify_service=None, jiosaavn_service=None):
    """
    Fetches/extracts audio features for a track_obj, standardizes them, and saves if newly fetched.
    Priority: DB -> Spotify API -> Librosa.
    Returns a list of feature values in the order of STANDARD_SIMILARITY_FEATURES, or None.
    `spotify_service` and `jiosaavn_service` would be modules or objects providing necessary functions
    like sp_client.audio_features, search_jiosaavn, download_preview, extract_audio_features, update_song_custom_audio_features
    This avoids circular imports if those functions live in spotify_service.client.
    For this refactor, we assume these service functions are passed or accessible in a non-circular way.
    """
    features_to_standardize = None
    source_of_features = None

    if track_obj.audio_features and isinstance(track_obj.audio_features, dict) and 'source' in track_obj.audio_features:
        features_to_standardize = track_obj.audio_features
        source_of_features = track_obj.audio_features['source']
        logger.debug(f"Using existing features for track {track_obj.spotify_id} from source: {source_of_features}")

    # Try Spotify API if no features or if existing features are not from Spotify (or spotify_attempted/error)
    if not features_to_standardize or source_of_features not in ['spotify', 'librosa']:
        if track_obj.spotify_id and sp_client: # sp_client is the spotipy.Spotify instance
            try:
                logger.debug(f"Attempting to fetch Spotify features for track {track_obj.spotify_id}...")
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
                    track_obj.save(update_fields=['audio_features'])
                    features_to_standardize = track_obj.audio_features
                    source_of_features = 'spotify'
                    logger.info(f"Fetched and saved Spotify features for track {track_obj.spotify_id}")
                else:
                    track_obj.audio_features = {'source': 'spotify_no_data'}
                    track_obj.save(update_fields=['audio_features'])
                    logger.info(f"Spotify API returned no audio features for track {track_obj.spotify_id}")
            except Exception as e:
                logger.error(f"Error fetching Spotify features for {track_obj.spotify_id}: {str(e)}")
                track_obj.audio_features = {'source': 'spotify_error', 'error': str(e)}
                track_obj.save(update_fields=['audio_features'])

    # Fallback to Librosa if still no usable features
    if not features_to_standardize or source_of_features not in ['spotify', 'librosa']:
        logger.debug(f"Attempting Librosa feature extraction for track {track_obj.spotify_id} ({track_obj.title})...")
        preview_url_to_use = track_obj.preview_url

        # This part requires access to search_jiosaavn, download_preview, extract_audio_features, update_song_custom_audio_features
        # These are currently in services.spotify_service.client. To avoid circularity, they would need to be
        # passed as arguments, or this _get_standardized_features_for_track function itself needs to be
        # part of a module that can import them without circularity, or those helpers become more generic.
        # For now, this will be a conceptual placeholder for the Librosa part if spotify_service cannot be imported directly.

        # Conceptual placeholder for Librosa feature extraction if needed:
        # if spotify_service and jiosaavn_service: # If helper modules/functions are passed
        #     if not preview_url_to_use:
        #         try:
        #             artist_name = track_obj.artists.first().name if track_obj.artists.exists() else ""
        #             js_results = jiosaavn_service.search_jiosaavn(f"{track_obj.title} {artist_name}".strip(), limit=1)
        #             if js_results and js_results[0].get('preview_url'): preview_url_to_use = js_results[0]['preview_url']
        #         except Exception: pass
        #     if preview_url_to_use:
        #         dl_path = spotify_service.download_preview(preview_url_to_use, track_obj.spotify_id)
        #         if dl_path:
        #             mod_time = os.path.getmtime(dl_path) if os.path.exists(dl_path) else None
        #             lib_feats = spotify_service.extract_audio_features(dl_path, file_mod_time_for_key=mod_time) # extract_audio_features is cached
        #             if lib_feats:
        #                 spotify_service.update_song_custom_audio_features(track_obj, lib_feats) # This saves with 'source: librosa'
        #                 features_to_standardize = track_obj.audio_features; source_of_features = 'librosa'
        pass # End of conceptual Librosa placeholder

    if not features_to_standardize or source_of_features not in ['spotify', 'librosa']:
        logger.warning(f"Failed to obtain/extract any usable audio features for track {track_obj.spotify_id}")
        return None

    vec = []
    if source_of_features == 'spotify':
        for key in STANDARD_SIMILARITY_FEATURES:
            val = features_to_standardize.get(key)
            if key == 'loudness': val = _normalize_loudness(val)
            elif key == 'tempo': val = _normalize_tempo(val)
            elif key == 'key': val = float(val / 11) if val is not None else 0.5 # Max key is 11
            elif key == 'mode': val = float(val) if val is not None else 0.5 # Mode is 0 or 1
            vec.append(float(val) if val is not None else 0.5) # Default 0.5 for missing
    elif source_of_features == 'librosa':
        # This mapping needs to be more robust based on actual Librosa output.
        vec.extend([
            _normalize_tempo(features_to_standardize.get('tempo')),
            features_to_standardize.get('rmse_mean', 0.5), # proxy for energy
            0.5, # danceability placeholder
            0.5, # valence placeholder
            features_to_standardize.get('zero_crossing_rate_mean', 0.5), # crude proxy for acousticness
            0.5, # instrumentalness placeholder
            0.5, # liveness placeholder
            features_to_standardize.get('spectral_bandwidth_mean', 0.5), # crude proxy for speechiness
            features_to_standardize.get('rmse_mean', 0.5), # proxy for loudness
            0.5, # mode placeholder
            0.5  # key placeholder
        ])
        # Ensure vector has correct length, padding with 0.5 if necessary
        vec = (vec + [0.5] * len(STANDARD_SIMILARITY_FEATURES))[:len(STANDARD_SIMILARITY_FEATURES)]
    else:
        return None

    if len(vec) != len(STANDARD_SIMILARITY_FEATURES):
        logger.error(f"Feature vector length mismatch for track {track_obj.spotify_id}. Expected {len(STANDARD_SIMILARITY_FEATURES)}, got {len(vec)}")
        return None

    return vec
