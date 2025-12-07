import numpy as np
import librosa
import logging
import os

import requests
import yt_dlp
from django.conf import settings
import warnings

logger = logging.getLogger(__name__)


# Placeholder implementations due to codebase reset.
# These would normally involve yt-dlp, pydub, requests etc.

def search_youtube_for_track(track_title, artist_name):
    """
    Placeholder: Searches YouTube for a track.
    In a real implementation, this would use yt-dlp or YouTube API.
    """
    logger.info(
        f"[Placeholder] Searching YouTube for: {track_title} - {artist_name}")
    # return "https://www.youtube.com/watch?v=dQw4w9WgXcQ" # Rick Astley as placeholder
    # Prioritize official audio
    query = f"{track_title} {artist_name} official audio"
    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'default_search': 'ytsearch1',  # Search YouTube and get the first result
        'quiet': True,
        'no_warnings': True,
        # Only get the first video if a playlist is found by mistake
        'extract_flat': 'discard_in_playlist',
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_result = ydl.extract_info(query, download=False)
            if search_result and 'entries' in search_result and search_result['entries']:
                video_info = search_result['entries'][0]
                # Original URL from extractor
                video_url = video_info.get('url')
                if not video_url and 'webpage_url' in video_info:  # Fallback to webpage_url
                    video_url = video_info.get('webpage_url')
                logger.info(f"Found YouTube URL for '{query}': {video_url}")
                return video_url
            else:
                logger.warning(f"No YouTube results found for query: {query}")
                return None
    except Exception as e:
        logger.error(f"Error searching YouTube for '{query}': {str(e)}")
        return None


def download_audio_from_youtube_url(youtube_url, output_filename_stem, base_output_dir=None):
    """
    Downloads audio from a YouTube URL to a specified path (without extension).
    Returns the full path to the downloaded file (with extension) or None.
    The actual extension will be determined by yt-dlp (e.g., .webm, .m4a).
    """
    if base_output_dir is None:
        base_output_dir = os.path.join(
            settings.MEDIA_ROOT, 'temp_audio_downloads')  # Temporary holding spot

    os.makedirs(base_output_dir, exist_ok=True)

    # output_path_template will include the determined extension by yt-dlp
    output_path_template = os.path.join(
        base_output_dir, f"{output_filename_stem}.%(ext)s")

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_path_template,
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
    }

    downloaded_file_path = None
    actual_extension = None

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(youtube_url, download=True)
            actual_extension = info_dict.get(
                'ext', 'mp3')  # Default to mp3 if not found

            # yt-dlp might save with a different extension than requested in preferredcodec
            # if the original is already good or conversion fails.
            # We need to find the actual downloaded file.
            # The 'outtmpl' with '%(ext)s' helps, but it's good to confirm.
            # For simplicity, let's assume yt-dlp converts to mp3 due to postprocessor.
            # If not, this part needs to be more robust to find the actual file.
            downloaded_file_path = os.path.join(
                base_output_dir, f"{output_filename_stem}.{actual_extension}")

            if not os.path.exists(downloaded_file_path):
                # Try common audio extensions if the exact one isn't found (e.g. if preferredcodec wasn't met)
                possible_extensions = ['mp3', 'm4a', 'webm', 'ogg', 'wav']
                for ext_attempt in possible_extensions:
                    temp_path = os.path.join(
                        base_output_dir, f"{output_filename_stem}.{ext_attempt}")
                    if os.path.exists(temp_path):
                        downloaded_file_path = temp_path
                        actual_extension = ext_attempt
                        break
                if not os.path.exists(downloaded_file_path):
                    logger.error(
                        f"Downloaded audio file not found for '{youtube_url}' with stem '{output_filename_stem}' and assumed ext '{actual_extension}'. Looked for: {downloaded_file_path}")
                    return None

        logger.info(
            f"Audio downloaded successfully from '{youtube_url}' to '{downloaded_file_path}'")
        return downloaded_file_path
    except Exception as e:
        logger.error(f"Error downloading audio from '{youtube_url}': {str(e)}")
        # Clean up partially downloaded file if it exists and path is known
        if downloaded_file_path and os.path.exists(downloaded_file_path):
            try:
                os.remove(downloaded_file_path)
            except OSError:
                logger.error(
                    f"Could not remove partially downloaded file: {downloaded_file_path}")
        # Also clean up if the template path was used but failed
        potential_template_path = os.path.join(base_output_dir,
                                               # Assuming mp3 was target
                                               f"{output_filename_stem}.mp3")
        if os.path.exists(potential_template_path):
            try:
                os.remove(potential_template_path)
            except OSError:
                logger.error(
                    f"Could not remove partially downloaded file: {potential_template_path}")
        return None


# Ensure MEDIA_ROOT is defined in settings for placeholders to work if they try to write.
# Example: MEDIA_ROOT = BASE_DIR / 'media'
# These placeholders mainly return paths and don't *require* actual file content for the next step's logic,
# but creating empty files can make them more robust for path existence checks.


def extract_audio_features(audio_file_path: str) -> dict | None:
    """
    Extracts audio features from an audio file using Librosa.
    Optimized for speed: uses lower sample rate and only 15 seconds of audio.
    Returns a dictionary of features or None if extraction fails.
    """
    if not audio_file_path or not os.path.exists(audio_file_path):
        logger.error(
            f"Audio file not found for Librosa extraction: {audio_file_path}")
        return None

    try:
        # Suppress warnings about PySoundFile fallback and internal librosa deprecations
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", category=UserWarning, message="PySoundFile failed")
            warnings.filterwarnings(
                "ignore", category=FutureWarning, module="librosa.core.audio")
            # OPTIMIZATION: Use lower sample rate (16000 vs default 22050) and shorter duration
            # This significantly reduces computation time while maintaining accuracy for feature extraction
            y, sr = librosa.load(
                audio_file_path,
                sr=16000,  # Lower sample rate for faster processing
                duration=15,  # Only analyze first 15 seconds
                res_type='kaiser_fast',  # Faster resampling
                mono=True  # Ensure mono to reduce processing
            )
            if y is None or len(y) == 0:
                logger.error(
                    f"Librosa loaded empty audio for {audio_file_path}")
                return None
    except Exception as e:
        logger.error(
            f"Error loading audio file {audio_file_path}: {e}", exc_info=True)
        return None

    try:
        # OPTIMIZED: Use smaller hop length for faster computation
        hop_length = 512

        # Extract features - essential set only for faster processing
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr, hop_length=hop_length)

        # Calculate features with shared FFT computation (via STFT)
        stft = np.abs(librosa.stft(y, hop_length=hop_length))

        # RMS energy
        rmse = librosa.feature.rms(S=stft)

        # Spectral features from STFT
        spectral_centroid = librosa.feature.spectral_centroid(S=stft, sr=sr)
        spectral_bandwidth = librosa.feature.spectral_bandwidth(S=stft, sr=sr)
        spectral_rolloff = librosa.feature.spectral_rolloff(S=stft, sr=sr)

        # Zero crossing rate (doesn't use STFT)
        zero_crossing_rate = librosa.feature.zero_crossing_rate(
            y, hop_length=hop_length)

        # Chroma features
        chroma_stft = librosa.feature.chroma_stft(S=stft**2, sr=sr)

        # MFCCs - reduced to 7 for speed (still captures most variance)
        mfccs = librosa.feature.mfcc(
            y=y, sr=sr, n_mfcc=7, hop_length=hop_length)

        logger.info(
            f"Successfully extracted Librosa features (optimized) for: {audio_file_path}")
        return {
            'tempo': float(tempo) if tempo is not None else None,
            'chroma_stft_mean': float(np.mean(chroma_stft)) if chroma_stft is not None else None,
            'rmse_mean': float(np.mean(rmse)) if rmse is not None else None,
            'spectral_centroid_mean': float(np.mean(spectral_centroid)) if spectral_centroid is not None else None,
            'spectral_bandwidth_mean': float(np.mean(spectral_bandwidth)) if spectral_bandwidth is not None else None,
            'rolloff_mean': float(np.mean(spectral_rolloff)) if spectral_rolloff is not None else None,
            'zero_crossing_rate_mean': float(np.mean(zero_crossing_rate)) if zero_crossing_rate is not None else None,
            'mfcc_mean': float(np.mean(mfccs)) if mfccs is not None else None,
        }
    except Exception as e:
        logger.error(
            f"Error extracting Librosa features from {audio_file_path}: {e}", exc_info=True)
        return None


def download_preview(preview_url, track_id):  # Changed track_id to track_identifier
    """
    Downloads a preview MP3 from a URL.
    Saves it to MEDIA_ROOT/previews/track_identifier.mp3.
    Returns the file path if successful, None otherwise.
    """
    if not preview_url or not preview_url.startswith("http"):
        print(f"Invalid preview URL for {track_id}: {preview_url}")
        return None

    # Construct file path using MEDIA_ROOT from settings
    # Ensure previews directory exists
    previews_dir = os.path.join(settings.MEDIA_ROOT, 'previews')
    os.makedirs(previews_dir, exist_ok=True)

    # Check if it's a YouTube URL (webpage_url from client.py)
    if "youtube.com" in preview_url or "youtu.be" in preview_url:
        # Use yt-dlp to download
        try:
            # We need to pass a filename stem. track_id is good.
            # download_audio_from_youtube_url saves to temp_audio_downloads by default,
            # but we want it in 'previews'.
            # Let's reuse download_audio_from_youtube_url but pass the previews dir.

            # The function download_audio_from_youtube_url handles extension automatically.
            # But extract_audio_features expects a path.
            # And download_preview is expected to return a path.

            downloaded_path = download_audio_from_youtube_url(
                preview_url,
                track_id,
                base_output_dir=previews_dir
            )

            if downloaded_path:
                # Rename to match expected format if needed?
                # download_preview callers expect a path, they don't strictly enforce extension
                # (except maybe if they assume .mp3).
                # extract_audio_features uses librosa, which handles many formats.
                # So returning the path with whatever extension yt-dlp gave is fine.
                return downloaded_path
            else:
                print(f"yt-dlp download failed for {track_id}")
                return None
        except Exception as e:
            print(f"Error downloading YouTube preview for {track_id}: {e}")
            return None

    file_path = os.path.join(previews_dir, f'{track_id}.mp3')

    # Avoid re-downloading if file already exists, unless forced
    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        # print(f"Preview already exists for {track_identifier} at {file_path}")
        return file_path

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(preview_url, stream=True,
                                timeout=10, headers=headers)  # 10-second timeout
        response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)

        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        if os.path.getsize(file_path) > 0:  # Check if something was written
            # print(f"Preview downloaded for {track_identifier} to {file_path}")
            return file_path
        else:
            # Downloaded an empty file, treat as failure
            print(
                f"Downloaded empty preview file for {track_id} from {preview_url}")
            if os.path.exists(file_path):  # Clean up empty file
                os.remove(file_path)
            return None

    except requests.exceptions.RequestException as e:  # Catch specific requests errors
        print(
            f"Error downloading preview for {track_id} from {preview_url}: {e}")
        # Clean up if partial/empty file created
        if os.path.exists(file_path) and os.path.getsize(file_path) == 0:
            os.remove(file_path)
        return None
    except IOError as e:  # Catch file system errors
        print(f"File error during preview download for {track_id}: {e}")
        return None
    except Exception as e:  # Catch any other unexpected errors
        print(f"Unexpected error downloading preview for {track_id}: {e}")
        return None
