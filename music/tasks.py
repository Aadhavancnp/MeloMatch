import os
import logging

from celery import shared_task
from django.conf import settings

from services.audioprocessing.audioprocessing import extract_audio_features, \
    download_preview
from .models import Track

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def fetch_youtube_preview_task(self, track_id, query):
    """
    YouTube-only fallback task. Called when JioSaavn fails/times out.
    This is slow (30-60s) so it ONLY runs in background.
    
    Args:
        track_id: Primary key of the Track model
        query: Search query string (track name + artist)
    """
    try:
        track = Track.objects.get(pk=track_id)
    except Track.DoesNotExist:
        logger.error(f"Track {track_id} not found for YouTube preview.")
        return False

    # Skip if already has valid preview (JioSaavn might have found one since queued)
    if track.preview_url and not track.preview_url.startswith('https://www.youtube.com'):
        logger.info(f"Track {track_id} already has preview URL, skipping YouTube.")
        return True

    try:
        from services.youtube_service.client import get_youtube_audio_url
        from services.audioprocessing.audioprocessing import download_audio_from_youtube_url
        from pydub import AudioSegment

        logger.info(f"YouTube search for track {track_id}: {query}")

        # Search YouTube
        youtube_url = get_youtube_audio_url(f"{query} official audio")
        if not youtube_url:
            logger.warning(f"No YouTube result for track {track_id}")
            return False

        # Download full audio to temp directory
        file_identifier = track.spotify_id or str(track.id)
        temp_dir = os.path.join(settings.MEDIA_ROOT, 'temp_previews')
        os.makedirs(temp_dir, exist_ok=True)

        temp_file = download_audio_from_youtube_url(
            youtube_url,
            f"yt_{file_identifier}",
            base_output_dir=temp_dir
        )

        if not temp_file or not os.path.exists(temp_file):
            logger.error(f"YouTube download failed for track {track_id}")
            return False

        try:
            # Cut to first 30 seconds
            audio = AudioSegment.from_file(temp_file)
            preview_audio = audio[:30000]

            # Save as preview
            previews_dir = os.path.join(settings.MEDIA_ROOT, 'previews')
            os.makedirs(previews_dir, exist_ok=True)
            preview_path = os.path.join(previews_dir, f"{file_identifier}_preview.mp3")
            preview_audio.export(preview_path, format="mp3", bitrate="128k")

            # Update track with preview URL
            preview_url = f"/media/previews/{file_identifier}_preview.mp3"
            track.preview_url = preview_url
            track.save(update_fields=['preview_url'])

            logger.info(f"Created YouTube preview for track {track_id}")

            # Clean up temp file
            os.remove(temp_file)

            # Extract audio features inline
            try:
                features = extract_audio_features(preview_path)
                if features:
                    track.audio_features = features
                    track.save(update_fields=['audio_features'])
                    logger.info(f"Extracted features for track {track_id}")
            except Exception as fe:
                logger.debug(f"Feature extraction failed for track {track_id}: {fe}")

            return True

        except Exception as cut_error:
            logger.error(f"Error cutting audio for track {track_id}: {cut_error}")
            if os.path.exists(temp_file):
                os.remove(temp_file)
            return False

    except Exception as e:
        logger.error(f"YouTube preview task failed for track {track_id}: {e}")
        try:
            raise self.retry(exc=e)
        except self.MaxRetriesExceededError:
            logger.error(f"Max retries exceeded for track {track_id}")
            return False


@shared_task(bind=True, max_retries=1, default_retry_delay=30)
def fetch_preview_url_task(self, track_id, query):
    """
    JioSaavn-first preview fetch with YouTube fallback.
    Called from background (playlist import, etc.)
    
    Args:
        track_id: Primary key of the Track model
        query: Search query string (track name + artist)
    """
    try:
        track = Track.objects.get(pk=track_id)
    except Track.DoesNotExist:
        logger.error(f"Track {track_id} not found for preview fetch.")
        return False

    # Skip if already has valid preview
    if track.preview_url and not track.preview_url.startswith('https://www.youtube.com'):
        logger.info(f"Track {track_id} already has preview URL, skipping.")
        return True

    preview_url = None
    
    # Strategy 1: Try JioSaavn first (faster, ~2-3s)
    try:
        from services.jiosaavn_service.api_client import search_songs, find_best_matching_song
        
        target_title = track.title
        target_artists = [artist.name for artist in track.artists.all()]
        
        search_results = search_songs(query, limit=5)
        if search_results:
            result = find_best_matching_song(
                search_results=search_results,
                target_title=target_title,
                target_artists=target_artists,
                min_artist_similarity=0.3,
                min_title_similarity=0.4
            )
            
            if result and result.get('preview_url'):
                preview_url = result['preview_url']
                logger.info(f"Found JioSaavn preview for track {track_id}")
    except Exception as e:
        logger.debug(f"JioSaavn failed for track {track_id}: {e}")

    # Strategy 2: YouTube fallback (slow, ~30-60s)
    if not preview_url:
        try:
            from services.youtube_service.client import get_youtube_audio_url
            youtube_url = get_youtube_audio_url(f"{query} official audio")
            if youtube_url:
                preview_url = youtube_url
                logger.info(f"Found YouTube preview for track {track_id}")
        except Exception as e:
            logger.debug(f"YouTube failed for track {track_id}: {e}")

    # Save if found
    if preview_url:
        track.preview_url = preview_url
        track.save(update_fields=['preview_url'])
        
        # Extract features inline (not queued)
        if not track.audio_features:
            try:
                features = extract_audio_features(download_preview(preview_url, track.spotify_id or str(track.id)))
                if features:
                    track.audio_features = features
                    track.save(update_fields=['audio_features'])
                    logger.info(f"Extracted features for track {track_id}")
            except Exception as fe:
                logger.debug(f"Feature extraction failed for track {track_id}: {fe}")
        
        return True
    
    return False


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def generate_youtube_preview_task(self, track_id, query):
    """
    DEPRECATED: Use fetch_youtube_preview_task instead.
    Kept for backward compatibility with queued tasks.
    """
    return fetch_youtube_preview_task(track_id, query)


@shared_task
def extract_track_features_task(track_id, preview_url_override=None):
    """
    Celery task to extract audio features for a track.
    Uses Librosa for feature extraction from a downloaded preview.
    Optimized to avoid unnecessary downloads for local files.
    """
    try:
        track = Track.objects.get(pk=track_id)
    except Track.DoesNotExist:
        print(f"Track with id {track_id} not found for feature extraction.")
        return False

    # Check if audio features already exist
    if track.audio_features:
        print(
            f"Track {track_id} already has audio features, skipping extraction.")
        return True

    preview_url_to_use = preview_url_override if preview_url_override else track.preview_url

    if not preview_url_to_use:
        print(
            f"No preview URL for track {track_id} ({track.title}) to extract features.")
        return False

    file_identifier = track.spotify_id if track.spotify_id else str(track.id)
    preview_file_path = None
    is_temp_file = False

    try:
        # OPTIMIZATION: If the preview URL is already a local file, use it directly
        if preview_url_to_use.startswith('/media/'):
            # Local file - construct the absolute path
            local_path = os.path.join(
                settings.MEDIA_ROOT,
                preview_url_to_use.replace('/media/', '', 1)
            )
            if os.path.exists(local_path):
                preview_file_path = local_path
                is_temp_file = False
                print(
                    f"Using existing local preview for track {track_id}: {local_path}")
            else:
                print(
                    f"Local preview file not found for track {track_id}: {local_path}")
                return False
        else:
            # External URL - need to download
            print(
                f"Downloading preview for track {track_id} from {preview_url_to_use}")
            preview_file_path = download_preview(
                preview_url_to_use, file_identifier)
            is_temp_file = True  # Mark for cleanup

        if preview_file_path and os.path.exists(preview_file_path):
            print(
                f"Extracting features for track {track_id} from {preview_file_path}")
            features = extract_audio_features(preview_file_path)
            if features:
                track.audio_features = features
                track.save(update_fields=['audio_features'])
                print(
                    f"Successfully extracted and saved features for track {track_id}")
                return True
            else:
                print(
                    f"Feature extraction returned no features for track {track_id}.")
                return False
        else:
            print(
                f"Preview download failed or file not found for track {track_id}.")
            return False

    except Exception as e:
        print(
            f"Error in extract_track_features_task for track {track_id}: {e}")
        return False
    finally:
        # Only clean up temporary downloaded files, not permanent local previews
        if is_temp_file and preview_file_path and os.path.exists(preview_file_path):
            try:
                os.remove(preview_file_path)
                print(
                    f"Cleaned up temp preview file {preview_file_path} for track {track_id}")
            except OSError as e:
                print(
                    f"Error deleting preview file {preview_file_path} for track {track_id}: {e}")
