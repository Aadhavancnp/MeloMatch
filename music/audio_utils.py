import os
import librosa
import numpy as np
import requests
from django.conf import settings

# This cache import might be problematic if tiered_cache is not a simple function decorator
# and relies on Django's setup, which might not be fully available when this module is first imported
# by Celery workers before Django is fully initialized.
# For functions used by Celery tasks, it's often safer to avoid heavy Django-dependent decorators
# or ensure they handle being called in a pre-Django-setup environment.
# from core.cache import tiered_cache # Assuming this is okay, otherwise remove for task-used functions

# If these functions are only used by Celery tasks and those tasks are Django-aware (e.g. @shared_task from Celery's Django integration),
# then using Django features like settings should be fine.

# @tiered_cache(maxsize=100) # Consider implications for Celery tasks. Caching here might be at worker level.
def extract_audio_features(audio_file):
    """
    Extracts audio features from an audio file using Librosa.
    """
    try:
        y, sr = librosa.load(audio_file, duration=30, res_type='kaiser_fast')
    except Exception as e:
        print(f"Error loading audio file {audio_file}: {e}")
        return None

    try:
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        chroma_stft = librosa.feature.chroma_stft(y=y, sr=sr)
        rmse = librosa.feature.rms(y=y)
        spec_cent = librosa.feature.spectral_centroid(y=y, sr=sr)
        spec_bw = librosa.feature.spectral_bandwidth(y=y, sr=sr)
        rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)
        zcr = librosa.feature.zero_crossing_rate(y)
        mfcc = librosa.feature.mfcc(y=y, sr=sr)

        return {
            'tempo': float(tempo) if tempo is not None else None,
            'chroma_stft_mean': float(np.mean(chroma_stft)) if chroma_stft is not None else None,
            'rmse_mean': float(np.mean(rmse)) if rmse is not None else None,
            'spectral_centroid_mean': float(np.mean(spec_cent)) if spec_cent is not None else None,
            'spectral_bandwidth_mean': float(np.mean(spec_bw)) if spec_bw is not None else None,
            'rolloff_mean': float(np.mean(rolloff)) if rolloff is not None else None,
            'zero_crossing_rate_mean': float(np.mean(zcr)) if zcr is not None else None,
            'mfcc_mean': float(np.mean(mfcc)) if mfcc is not None else None,
        }
    except Exception as e:
        print(f"Error extracting Librosa features from {audio_file}: {e}")
        return None


def download_preview(preview_url, track_identifier): # Changed track_id to track_identifier
    """
    Downloads a preview MP3 from a URL.
    Saves it to MEDIA_ROOT/previews/track_identifier.mp3.
    Returns the file path if successful, None otherwise.
    """
    if not preview_url or not preview_url.startswith("http"):
        print(f"Invalid preview URL for {track_identifier}: {preview_url}")
        return None

    # Construct file path using MEDIA_ROOT from settings
    # Ensure previews directory exists
    previews_dir = os.path.join(settings.MEDIA_ROOT, 'previews')
    os.makedirs(previews_dir, exist_ok=True)

    file_path = os.path.join(previews_dir, f'{track_identifier}.mp3')

    # Avoid re-downloading if file already exists, unless forced
    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        # print(f"Preview already exists for {track_identifier} at {file_path}")
        return file_path

    try:
        response = requests.get(preview_url, stream=True, timeout=10) # 10-second timeout
        response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)

        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        if os.path.getsize(file_path) > 0: # Check if something was written
            # print(f"Preview downloaded for {track_identifier} to {file_path}")
            return file_path
        else:
            # Downloaded an empty file, treat as failure
            print(f"Downloaded empty preview file for {track_identifier} from {preview_url}")
            if os.path.exists(file_path): # Clean up empty file
                os.remove(file_path)
            return None

    except requests.exceptions.RequestException as e: # Catch specific requests errors
        print(f"Error downloading preview for {track_identifier} from {preview_url}: {e}")
        if os.path.exists(file_path) and os.path.getsize(file_path) == 0: # Clean up if partial/empty file created
             os.remove(file_path)
        return None
    except IOError as e: # Catch file system errors
        print(f"File error during preview download for {track_identifier}: {e}")
        return None
    except Exception as e: # Catch any other unexpected errors
        print(f"Unexpected error downloading preview for {track_identifier}: {e}")
        return None
