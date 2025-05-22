import os
import yt_dlp
from pydub import AudioSegment
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

def search_youtube_for_track(track_title, artist_name):
    """
    Searches YouTube for a track and returns the URL of the first video result.
    """
    query = f"{track_title} {artist_name} official audio" # Prioritize official audio
    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'default_search': 'ytsearch1', # Search YouTube and get the first result
        'quiet': True,
        'no_warnings': True,
        'extract_flat': 'discard_in_playlist', # Only get the first video if a playlist is found by mistake
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_result = ydl.extract_info(query, download=False)
            if search_result and 'entries' in search_result and search_result['entries']:
                video_info = search_result['entries'][0]
                video_url = video_info.get('url') # Original URL from extractor
                if not video_url and 'webpage_url' in video_info: # Fallback to webpage_url
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
        base_output_dir = os.path.join(settings.MEDIA_ROOT, 'temp_audio_downloads') # Temporary holding spot
    
    os.makedirs(base_output_dir, exist_ok=True)
    
    # output_path_template will include the determined extension by yt-dlp
    output_path_template = os.path.join(base_output_dir, f"{output_filename_stem}.%(ext)s")

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_path_template,
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio', # Ensures we only get audio
            'preferredcodec': 'mp3', # Request mp3, but might get something else if not possible
            'preferredquality': '192',
        }],
        # 'ffmpeg_location': '/path/to/ffmpeg' # Optional: if ffmpeg is not in PATH
    }

    downloaded_file_path = None
    actual_extension = None

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(youtube_url, download=True)
            actual_extension = info_dict.get('ext', 'mp3') # Default to mp3 if not found
            
            # yt-dlp might save with a different extension than requested in preferredcodec
            # if the original is already good or conversion fails.
            # We need to find the actual downloaded file.
            # The 'outtmpl' with '%(ext)s' helps, but it's good to confirm.
            # For simplicity, let's assume yt-dlp converts to mp3 due to postprocessor.
            # If not, this part needs to be more robust to find the actual file.
            downloaded_file_path = os.path.join(base_output_dir, f"{output_filename_stem}.{actual_extension}")
            
            if not os.path.exists(downloaded_file_path):
                 # Try common audio extensions if the exact one isn't found (e.g. if preferredcodec wasn't met)
                possible_extensions = ['mp3', 'm4a', 'webm', 'ogg', 'wav']
                for ext_attempt in possible_extensions:
                    temp_path = os.path.join(base_output_dir, f"{output_filename_stem}.{ext_attempt}")
                    if os.path.exists(temp_path):
                        downloaded_file_path = temp_path
                        actual_extension = ext_attempt
                        break
                if not os.path.exists(downloaded_file_path):
                    logger.error(f"Downloaded audio file not found for '{youtube_url}' with stem '{output_filename_stem}' and assumed ext '{actual_extension}'. Looked for: {downloaded_file_path}")
                    return None

        logger.info(f"Audio downloaded successfully from '{youtube_url}' to '{downloaded_file_path}'")
        return downloaded_file_path
    except Exception as e:
        logger.error(f"Error downloading audio from '{youtube_url}': {str(e)}")
        # Clean up partially downloaded file if it exists and path is known
        if downloaded_file_path and os.path.exists(downloaded_file_path):
            try:
                os.remove(downloaded_file_path)
            except OSError:
                logger.error(f"Could not remove partially downloaded file: {downloaded_file_path}")
        # Also clean up if the template path was used but failed
        potential_template_path = os.path.join(base_output_dir, f"{output_filename_stem}.mp3") # Assuming mp3 was target
        if os.path.exists(potential_template_path):
             try:
                os.remove(potential_template_path)
             except OSError:
                logger.error(f"Could not remove partially downloaded file: {potential_template_path}")
        return None

def extract_30_second_clip(input_audio_path, output_clip_path_stem, desired_format="mp3"):
    """
    Extracts a 30-second clip from an audio file using pydub.
    Tries to take a segment from the middle, or the beginning if the track is short.
    Exports to the desired_format (e.g., "mp3").
    Returns the full path to the output clip or None.
    """
    try:
        # Ensure output directory exists
        output_dir = os.path.dirname(output_clip_path_stem)
        os.makedirs(output_dir, exist_ok=True)
        
        final_output_path = f"{output_clip_path_stem}.{desired_format}"

        audio = AudioSegment.from_file(input_audio_path)
        duration_ms = len(audio)
        
        # Determine start and end points for the 30-second clip
        clip_duration_ms = 30 * 1000
        
        if duration_ms <= clip_duration_ms:
            # If track is shorter than or equal to 30s, take the whole track
            start_ms = 0
            end_ms = duration_ms
        else:
            # Try to take a clip from around 1/3rd into the song, but not too close to the start
            # e.g., start at 30s if long enough, or a bit earlier if needed
            potential_start_ms = min(duration_ms // 3, 30000) # Start at 30s or 1/3rd in, whichever is smaller
            if duration_ms - potential_start_ms < clip_duration_ms: # Not enough left for a full clip
                start_ms = duration_ms - clip_duration_ms # Take last 30s
            else:
                start_ms = potential_start_ms
            end_ms = start_ms + clip_duration_ms

        extracted_clip = audio[start_ms:end_ms]
        
        # Export the clip
        extracted_clip.export(final_output_path, format=desired_format)
        logger.info(f"Successfully extracted {int((end_ms - start_ms)/1000)}s clip to '{final_output_path}'")
        return final_output_path
    except Exception as e:
        logger.error(f"Error extracting clip from '{input_audio_path}': {str(e)}")
        return None
