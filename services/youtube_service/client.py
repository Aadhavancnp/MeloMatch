"""
YouTube Service Client - Async Implementation

Provides methods to search and retrieve audio URLs from YouTube using yt-dlp.
The yt-dlp library is synchronous, so async methods use run_in_executor.
"""
import logging
from typing import Optional

import yt_dlp

from services.async_utils import run_in_executor

logger = logging.getLogger(__name__)


def get_youtube_audio_url(query: str) -> Optional[str]:
    """
    Searches YouTube for a video matching the query and returns a direct audio stream URL.
    This performs a two-step process: search for video, then extract audio URL.

    Args:
        query: Search query string

    Returns:
        Direct audio stream URL or None if not found
    """
    # Force YouTube search - get first result only
    if not query.startswith('ytsearch'):
        query = f"ytsearch1:{query}"

    # Step 1: Search for the video (flat extraction for speed)
    search_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'socket_timeout': 10,
        'retries': 1,
    }

    video_url = None
    try:
        with yt_dlp.YoutubeDL(search_opts) as ydl:
            logger.info(f"Searching YouTube for: {query}")
            info = ydl.extract_info(query, download=False)

            if info and 'entries' in info and info['entries']:
                video_info = info['entries'][0]

                # Check if we need to drill down
                if 'entries' in video_info:
                    video = video_info['entries'][0]
                else:
                    video = video_info

                video_url = video.get('webpage_url')
                if not video_url and video.get('id'):
                    video_url = f"https://www.youtube.com/watch?v={video.get('id')}"
            else:
                # Fallback if 'entries' structure is not as expected
                video = info
                video_url = video.get('webpage_url')
                if not video_url and video.get('id'):
                    video_url = f"https://www.youtube.com/watch?v={video.get('id')}"

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"YouTube download error for '{query}': {e}")
        return None
    except Exception as e:
        logger.error(f"Error searching YouTube for '{query}': {e}")
        return None

    if not video_url:
        logger.warning(f"No YouTube video found for '{query}'")
        return None

    # Step 2: Extract the direct audio stream URL
    extract_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 15,
        'retries': 1,
    }

    try:
        with yt_dlp.YoutubeDL(extract_opts) as ydl:
            logger.info(f"Extracting audio URL from: {video_url}")
            info = ydl.extract_info(video_url, download=False)
            if info and info.get('url'):
                audio_url = info['url']
                logger.info(f"Got direct audio URL for '{query}'")
                return audio_url
    except Exception as e:
        logger.error(f"Error extracting audio URL from '{video_url}': {e}")

    return None


def get_youtube_direct_audio_url(video_url: str) -> Optional[str]:
    """
    Extracts the direct audio stream URL from a YouTube video URL.

    Args:
        video_url: YouTube video URL

    Returns:
        Direct audio stream URL or None
    """
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            if info and info.get('url'):
                return info['url']
    except Exception as e:
        logger.error(f"Error extracting audio URL from '{video_url}': {e}")

    return None


# =============================================================================
# ASYNC WRAPPER FUNCTIONS
# =============================================================================

async def async_get_youtube_audio_url(query: str) -> Optional[str]:
    """
    Async wrapper for get_youtube_audio_url.
    Runs yt-dlp in a thread executor since it's a blocking I/O operation.

    Args:
        query: Search query string

    Returns:
        YouTube video URL or None if not found
    """
    return await run_in_executor(get_youtube_audio_url, query)


async def async_get_youtube_direct_audio_url(video_url: str) -> Optional[str]:
    """
    Async wrapper for get_youtube_direct_audio_url.

    Args:
        video_url: YouTube video URL

    Returns:
        Direct audio stream URL or None
    """
    return await run_in_executor(get_youtube_direct_audio_url, video_url)
