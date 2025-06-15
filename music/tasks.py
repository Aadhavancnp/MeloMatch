from celery import shared_task
from .models import Track
from .spotify import extract_audio_features, download_preview # librosa related
from django.conf import settings
import os

@shared_task
def extract_track_features_task(track_id, preview_url_override=None):
    """
    Celery task to extract audio features for a track.
    Uses Librosa for feature extraction from a downloaded preview.
    """
    try:
        # It's generally better to pass primary keys (like track.id) to tasks,
        # rather than potentially large model instances.
        track = Track.objects.get(pk=track_id)
    except Track.DoesNotExist:
        print(f"Track with id {track_id} not found for feature extraction.")
        return False # Indicate failure or that track was not found

    preview_url_to_use = preview_url_override if preview_url_override else track.preview_url

    if not preview_url_to_use:
        print(f"No preview URL for track {track_id} ({track.title}) to extract features.")
        return False # Indicate that features could not be extracted due to missing URL

    # Determine a unique identifier for the filename.
    # Using spotify_id is good if available, otherwise fallback to internal pk.
    file_identifier = track.spotify_id if track.spotify_id else str(track.id)

    # Ensure directory for previews exists (download_preview might also do this)
    # previews_dir = os.path.join(settings.MEDIA_ROOT, 'previews')
    # os.makedirs(previews_dir, exist_ok=True)

    preview_file_path = None # Initialize
    try:
        print(f"Downloading preview for track {track_id} from {preview_url_to_use}")
        preview_file_path = download_preview(preview_url_to_use, file_identifier)

        if preview_file_path and os.path.exists(preview_file_path):
            print(f"Extracting features for track {track_id} from {preview_file_path}")
            features = extract_audio_features(preview_file_path) # librosa call
            if features:
                track.audio_features = features
                track.save()
                print(f"Successfully extracted and saved features for track {track_id}")
                return True # Indicate success
            else:
                print(f"Feature extraction returned no features for track {track_id}.")
                return False # Indicate feature extraction failure
        else:
            print(f"Preview download failed or file not found for track {track_id}.")
            return False # Indicate download failure

    except Exception as e:
        print(f"Error in extract_track_features_task for track {track_id}: {e}")
        # Consider re-raising or specific error handling if retries are needed
        return False # Indicate generic error
    finally:
        # Optionally remove preview_file_path after extraction
        if preview_file_path and os.path.exists(preview_file_path):
            try:
                os.remove(preview_file_path)
                print(f"Cleaned up preview file {preview_file_path} for track {track_id}")
            except OSError as e:
                print(f"Error deleting preview file {preview_file_path} for track {track_id}: {e}")
