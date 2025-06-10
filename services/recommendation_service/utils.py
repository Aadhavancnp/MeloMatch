import logging
import os
from django.conf import settings
import numpy as np # For the default feature vector

# Assuming models are in music.models. Adjust if necessary.
from music.models import Track, LocalAudioClip

# Placeholder imports for functions that would normally come from other service modules
# These paths might need adjustment if the actual files are recreated elsewhere or have different names.

logger = logging.getLogger(__name__) # Moved logger definition to the top

try:
    from services.jiosaavn_service.api_client import get_song_details as get_jiosaavn_song_details
except ImportError:
    logger.warning("Could not import get_jiosaavn_song_details. Using placeholder.")
    def get_jiosaavn_song_details(song_id):
        logger.info(f"[Placeholder] Recommendation service calling placeholder get_jiosaavn_song_details for id: {song_id}")
        if song_id == "dummy_jiosaavn_id_with_preview": # Match the one in the actual placeholder file
            return {'preview_url': 'http://example.com/dummy_preview.mp3'}
        return None

try:
    from services.audioprocessing import (
        search_youtube_for_track,
        download_audio_from_youtube_url,
        download_url_to_tempfile
    )
except ImportError:
    logger.warning("Could not import audioprocessing functions. Using placeholders.")
    def search_youtube_for_track(track_title, artist_name): return None
    def download_audio_from_youtube_url(youtube_url, output_filename_stem): return None
    def download_url_to_tempfile(url, filename_stem): return None

# logger = logging.getLogger(__name__) # Moved to top

# Standard set of features used for cosine similarity if features are numeric
# This list needs to match the features extracted by Librosa and stored.
# Updating to include individual MFCC means as per new extract_audio_features output.
STANDARD_SIMILARITY_FEATURES = [
    'tempo', 'chroma_stft_mean', 'rmse_mean',
    'spectral_centroid_mean', 'spectral_bandwidth_mean',
    'rolloff_mean', 'zero_crossing_rate_mean'
] + [f'mfcc{i+1}_mean' for i in range(13)] # Using 13 MFCC means, common practice
# Variances could also be added if desired for more detail, e.g. 'rmse_var', 'mfcc1_var' etc.

NUM_STANDARD_FEATURES = len(STANDARD_SIMILARITY_FEATURES)

# Import the actual Librosa extraction function
try:
    from services.audioprocessing import extract_audio_features as librosa_extract_audio_features
except ImportError:
    logger.critical("CRITICAL: Could not import actual librosa_extract_audio_features from services.audioprocessing. Feature extraction will fail.")
    def librosa_extract_audio_features(audio_file_path: str) -> dict | None: # Placeholder if import fails
        logger.error(f"[Placeholder] librosa_extract_audio_features called for {audio_file_path} but real function not imported.")
        return None


def _normalize_tempo(tempo, min_bpm=60, max_bpm=180):
    """Normalizes tempo to a 0-1 range. Assumes tempo is in BPM."""
    if tempo is None: return 0.0 # Default for missing tempo
    tempo = float(tempo)
    if tempo < min_bpm: tempo = min_bpm
    if tempo > max_bpm: tempo = max_bpm
    return (tempo - min_bpm) / (max_bpm - min_bpm)


def _get_audio_file_for_librosa(track_obj: Track) -> tuple[str | None, str | None]:
    """
    Finds or downloads a preview audio file for a given track object for Librosa processing.
    Returns: Tuple (Path to a local audio file or None, source_type_string or None).
    """
    if not isinstance(track_obj, Track):
        logger.error(f"_get_audio_file_for_librosa: track_obj is not a Track instance: {track_obj}")
        return None, None

    logger.info(f"_get_audio_file_for_librosa: Attempting to find audio for track '{track_obj.title}' (ID: {track_obj.id})")

    source_type_for_features = None
    downloaded_path = None

    # Priority 1: Existing LocalAudioClip
    suitable_source_types = ['youtube_download', 'jiosaavn_download', 'spotify_preview_dl', 'uploaded_full']
    local_clip = LocalAudioClip.objects.filter(track=track_obj, source_type__in=suitable_source_types).first()
    if local_clip and local_clip.audio_file and hasattr(local_clip.audio_file, 'path') and os.path.exists(local_clip.audio_file.path):
        logger.info(f"Found existing suitable LocalAudioClip for track {track_obj.id}: {local_clip.audio_file.path}")
        source_type_for_features = f"librosa_{local_clip.source_type}"
        return local_clip.audio_file.path, source_type_for_features

    filename_stem = f"track_{track_obj.id}_temp"

    # Priority 2: Track.preview_url
    if track_obj.preview_url:
        logger.info(f"Attempting to download track.preview_url for track {track_obj.id}: {track_obj.preview_url}")
        downloaded_path = download_url_to_tempfile(track_obj.preview_url, filename_stem)
        if downloaded_path and os.path.exists(downloaded_path):
            logger.info(f"Successfully downloaded from track.preview_url to {downloaded_path}")
            source_type_for_features = "librosa_spotify_preview" # Assuming preview_url is usually from Spotify
            return downloaded_path, source_type_for_features
        else:
            logger.warning(f"Failed to download or verify file from track.preview_url for {track_obj.id}")

    # Priority 3: JioSaavn Download URL
    jiosaavn_id = getattr(track_obj, 'jiosaavn_id', None)
    if not jiosaavn_id and "jiosaavn" in (track_obj.spotify_id or "").lower(): # Crude check
        jiosaavn_id = track_obj.spotify_id

    if jiosaavn_id:
        logger.info(f"Track {track_obj.id} (JioSaavn ID: {jiosaavn_id}). Attempting to get details.")
        jiosaavn_details = get_jiosaavn_song_details(jiosaavn_id) # Uses placeholder if service not found
        if jiosaavn_details and jiosaavn_details.get('preview_url'):
            logger.info(f"Attempting to download JioSaavn preview for {track_obj.id}: {jiosaavn_details['preview_url']}")
            downloaded_path = download_url_to_tempfile(jiosaavn_details['preview_url'], f"{filename_stem}_jiosaavn")
            if downloaded_path and os.path.exists(downloaded_path):
                logger.info(f"Successfully downloaded from JioSaavn preview_url to {downloaded_path}")
                source_type_for_features = "librosa_jiosaavn_preview"
                return downloaded_path, source_type_for_features
            else:
                logger.warning(f"Failed to download/verify file from JioSaavn preview_url for {track_obj.id}")

    # Priority 4: YouTube Download
    logger.info(f"Attempting YouTube for track {track_obj.id}: '{track_obj.title}'")
    track_artists_str = ", ".join([a.name for a in track_obj.artists.all()])
    youtube_url = search_youtube_for_track(track_obj.title, track_artists_str) # Uses placeholder
    if youtube_url:
        logger.info(f"Found YouTube URL for track {track_obj.id}: {youtube_url}. Attempting download.")
        downloaded_path = download_audio_from_youtube_url(youtube_url, output_filename_stem=f"track_{track_obj.id}_youtube") # Uses placeholder
        if downloaded_path and os.path.exists(downloaded_path):
            logger.info(f"Successfully downloaded from YouTube to {downloaded_path}")
            source_type_for_features = "librosa_youtube"
            return downloaded_path, source_type_for_features
        else:
            logger.warning(f"Failed to download/verify file from YouTube for {track_obj.id}")

    logger.warning(f"Could not source audio file for track {track_obj.id} ('{track_obj.title}') for Librosa processing.")
    return None, None


def _get_standardized_features_for_track(track_obj: Track, sp_client=None) -> np.ndarray | None:
    """
    Gets standardized audio features for a track.
    1. Checks for existing Librosa features on the Track model.
    2. If not found, sources an audio file using _get_audio_file_for_librosa.
    3. Extracts features using Librosa via services.audioprocessing.extract_audio_features.
    4. Saves these features (with source type) to track_obj.audio_features.
    5. Standardizes the features (either existing or newly extracted) into a NumPy vector.
    """
    if not isinstance(track_obj, Track):
        logger.error(f"_get_standardized_features_for_track: track_obj is not a Track instance: {track_obj}")
        return None

    # Step 1: Check Existing Librosa Features
    if track_obj.audio_features and isinstance(track_obj.audio_features, dict) and \
       track_obj.audio_features.get('source', '').startswith('librosa'):
        current_features = track_obj.audio_features
        logger.info(f"Using existing Librosa features from Track {track_obj.id} ('{track_obj.title}')")
    else:
        logger.info(f"No pre-existing Librosa features for Track {track_obj.id}. Attempting to source and extract.")
        audio_file_path, source_type = _get_audio_file_for_librosa(track_obj)

        if not audio_file_path:
            logger.warning(f"Could not obtain audio file for Librosa for track {track_obj.id}. Using zero vector.")
            return np.zeros(NUM_STANDARD_FEATURES) # Or return None

        logger.info(f"Audio file obtained for track {track_obj.id}: {audio_file_path} (source type for features: {source_type})")

        raw_librosa_features = librosa_extract_audio_features(audio_file_path)

        if raw_librosa_features:
            features_to_save = raw_librosa_features.copy()
            features_to_save['source'] = source_type if source_type else 'librosa_unknown_source'

            track_obj.audio_features = features_to_save
            try:
                track_obj.save(update_fields=['audio_features'])
                logger.info(f"Successfully extracted and saved Librosa features for track {track_obj.id} from source {source_type}.")
                current_features = features_to_save
            except Exception as e:
                logger.error(f"Error saving Librosa features to track {track_obj.id}: {e}", exc_info=True)
                # Proceed with features for this call, but they won't be saved for next time
                current_features = features_to_save
        else:
            logger.warning(f"Librosa feature extraction failed for track {track_obj.id} using file {audio_file_path}. Using zero vector.")
            # Optionally save an error state to audio_features to prevent re-tries for a known bad file?
            # track_obj.audio_features = {'source': 'librosa_error', 'error': 'extraction_failed'}
            # track_obj.save(update_fields=['audio_features'])
            return np.zeros(NUM_STANDARD_FEATURES) # Or return None

        # Clean up temporary downloaded file if it's from download_url_to_tempfile or youtube
        # This assumes paths from these functions are identifiable as temporary.
        # A more robust system would have these download functions return a flag or use a specific temp dir.
        if source_type and ("_preview" in source_type or "_youtube" in source_type): # Heuristic
            if os.path.exists(audio_file_path) and "temp_previews" in audio_file_path or "temp_youtube_downloads" in audio_file_path :
                try:
                    os.remove(audio_file_path)
                    logger.info(f"Cleaned up temporary audio file: {audio_file_path}")
                except OSError as e:
                    logger.error(f"Error cleaning up temporary audio file {audio_file_path}: {e}")


    # Step 4: Standardize features (either existing or newly extracted)
    if not current_features: # Should not happen if logic above is correct, but as a safeguard
        logger.error(f"Feature object 'current_features' is unexpectedly None for track {track_obj.id}. Returning zero vector.")
        return np.zeros(NUM_STANDARD_FEATURES)

    feature_vector = []
    for f_name in STANDARD_SIMILARITY_FEATURES:
        val = current_features.get(f_name, 0.0) # Default to 0.0 if feature not present
        if f_name == 'tempo':
            val = _normalize_tempo(val)
        # Add other normalizations here if needed for other features
        # e.g. rmse_mean could be normalized if its scale is too varied
        # elif f_name == 'rmse_mean':
        #     val = _normalize_rmse(val) # hypothetical
        feature_vector.append(float(val))

    logger.info(f"Generated standardized feature vector for track {track_obj.id}.")
    return np.array(feature_vector)


# Note: The sp_client parameter for _get_standardized_features_for_track is kept for now
# in case any part of the Librosa pipeline (e.g. fetching metadata if audio source fails)
# might need it, but direct sp.audio_features call is removed. If not needed by Librosa pipeline, it can be removed.
