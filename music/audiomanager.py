import os
import uuid
from django.conf import settings
from django.core.files import File
from .models import Track, LocalAudioClip
from .audioprocessing import search_youtube_for_track, download_audio_from_youtube_url, extract_30_second_clip
import logging
import requests

logger = logging.getLogger(__name__)

def get_or_create_local_audio_clip(track_id):
    """
    Tries to get an existing LocalAudioClip for a track.
    If not found, attempts to create one by:
    1. Downloading from Track.preview_url (if available).
    2. Searching YouTube, downloading, and extracting a clip.
    Returns the URL of the local audio clip or None.
    """
    try:
        track = Track.objects.get(spotify_id=track_id) # Assuming track_id is spotify_id
    except Track.DoesNotExist:
        logger.error(f"Track with spotify_id {track_id} not found.")
        return None

    # 1. Check for existing LocalAudioClip
    existing_clip = LocalAudioClip.objects.filter(track=track).first()
    if existing_clip and existing_clip.audio_file:
        logger.info(f"Found existing local clip for track {track.title}: {existing_clip.audio_file.url}")
        return existing_clip.audio_file.url

    # Define paths
    # Use a unique name for temporary downloaded full audio to avoid collisions
    temp_download_filename_stem = f"temp_{track.spotify_id}_{uuid.uuid4().hex[:8]}"
    # Store final clips in a structured path based on track ID
    final_clip_dir = os.path.join(settings.MEDIA_ROOT, 'track_previews', track.spotify_id[:2], track.spotify_id)
    os.makedirs(final_clip_dir, exist_ok=True)
    final_clip_filename_stem = "preview_30s" # Consistent name for the final 30s clip
    final_clip_path_stem = os.path.join(final_clip_dir, final_clip_filename_stem)


    # 2. Try Track.preview_url (if available)
    if track.preview_url:
        logger.info(f"Attempting to download from Track.preview_url: {track.preview_url} for track {track.title}")
        try:
            response = requests.get(track.preview_url, stream=True, timeout=10)
            response.raise_for_status()

            # Determine a temporary path for this downloaded preview
            temp_preview_download_path = os.path.join(settings.MEDIA_ROOT, 'temp_audio_downloads', f"{temp_download_filename_stem}_spotify.mp3")
            os.makedirs(os.path.dirname(temp_preview_download_path), exist_ok=True)

            with open(temp_preview_download_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            logger.info(f"Successfully downloaded from Track.preview_url to {temp_preview_download_path}")

            extracted_clip_path = extract_30_second_clip(temp_preview_download_path, final_clip_path_stem)

            if extracted_clip_path:
                with open(extracted_clip_path, 'rb') as clip_file:
                    django_file = File(clip_file, name=f"{final_clip_filename_stem}.mp3")
                    new_clip = LocalAudioClip.objects.create(
                        track=track,
                        audio_file=django_file,
                        source_url=track.preview_url,
                        source_type='spotify_preview_dl', # Or more generic like 'direct_preview_dl'
                        duration=30
                    )
                logger.info(f"Created LocalAudioClip from Track.preview_url for {track.title}: {new_clip.audio_file.url}")
                # Clean up temp downloaded full preview
                try:
                    os.remove(temp_preview_download_path)
                    if os.path.exists(extracted_clip_path) and extracted_clip_path != new_clip.audio_file.path:
                        # This case might happen if FileField renames on save, though unlikely with current naming
                        os.remove(extracted_clip_path)
                except OSError as e:
                    logger.error(f"Error cleaning up temporary file {temp_preview_download_path} or {extracted_clip_path}: {e}")
                return new_clip.audio_file.url
            else:
                logger.warning(f"Failed to extract clip from downloaded Track.preview_url for {track.title}")
                # Clean up failed download/extraction
                if os.path.exists(temp_preview_download_path): os.remove(temp_preview_download_path)

        except requests.exceptions.RequestException as e:
            logger.error(f"Error downloading Track.preview_url {track.preview_url}: {e}")
        except Exception as e: # Catch other errors during this block
            logger.error(f"Generic error processing Track.preview_url for {track.title}: {e}")


    # 3. Fallback to YouTube
    logger.info(f"No suitable local clip from Track.preview_url. Attempting YouTube fallback for track {track.title}")
    artist_name = track.artists.first().name if track.artists.exists() else ""
    youtube_url = search_youtube_for_track(track.title, artist_name)

    if youtube_url:
        logger.info(f"Found YouTube URL: {youtube_url}. Attempting download for track {track.title}")
        # temp_audio_downloads is where download_audio_from_youtube_url saves the initial full download
        downloaded_full_audio_path = download_audio_from_youtube_url(youtube_url, temp_download_filename_stem)

        if downloaded_full_audio_path:
            extracted_clip_path = extract_30_second_clip(downloaded_full_audio_path, final_clip_path_stem)

            if extracted_clip_path:
                with open(extracted_clip_path, 'rb') as clip_file:
                    django_file = File(clip_file, name=f"{final_clip_filename_stem}.mp3")
                    new_clip = LocalAudioClip.objects.create(
                        track=track,
                        audio_file=django_file,
                        source_url=youtube_url,
                        source_type='youtube',
                        duration=30
                    )
                logger.info(f"Created LocalAudioClip from YouTube for {track.title}: {new_clip.audio_file.url}")
                # Clean up temp downloaded full audio and the version used for Django File object
                try:
                    os.remove(downloaded_full_audio_path)
                    # If extracted_clip_path is different from where Django saved it (due to FileField storage logic)
                    # and it still exists, remove it.
                    if os.path.exists(extracted_clip_path) and extracted_clip_path != new_clip.audio_file.path:
                         os.remove(extracted_clip_path)
                except OSError as e:
                    logger.error(f"Error cleaning up temporary files {downloaded_full_audio_path} or {extracted_clip_path}: {e}")
                return new_clip.audio_file.url
            else:
                logger.warning(f"Failed to extract clip from YouTube download for {track.title}")
                # Clean up failed download if it exists
                if os.path.exists(downloaded_full_audio_path): os.remove(downloaded_full_audio_path)
        else:
            logger.warning(f"Failed to download audio from YouTube URL {youtube_url} for track {track.title}")
    else:
        logger.warning(f"No YouTube URL found for track {track.title}")

    logger.error(f"All attempts to get/create local audio clip failed for track {track.title} (ID: {track_id})")
    return None
