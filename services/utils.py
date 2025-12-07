import functools
import hashlib
import logging
import base64
import io

from PIL import Image
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile

logger = logging.getLogger(__name__)


def generate_cache_key(prefix, *args, **kwargs):
    """Generates a cache key based on prefix and function arguments."""
    key_parts = [str(prefix)]
    key_parts.extend(str(arg) for arg in args)

    for key, value in sorted(kwargs.items()):
        key_parts.append(f"{key}_{value}")

    raw_key = "_".join(key_parts)
    if len(raw_key) > 150:  # Avoid exceeding cache backend key length limits
        return f"{prefix}_hash_{hashlib.md5(raw_key.encode('utf-8')).hexdigest()}"
    return raw_key


def cache_api_call(key_prefix, timeout=3600, exclude_functions=None, skip_first_arg=True):
    """Decorator to cache API call results with specified timeout.
    
    Args:
        key_prefix: Prefix for cache key
        timeout: Cache timeout in seconds
        exclude_functions: List of function names to skip caching
        skip_first_arg: If True, skip first arg (typically client/request object).
                        Set to False for functions where first arg is important data.
    """

    # Default list of functions that should not be cached
    if exclude_functions is None:
        exclude_functions = ['get_spotify_client']

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Skip caching for excluded functions
            if func.__name__ in exclude_functions:
                logger.debug(f"Skipping cache for excluded function: {func.__name__}")
                return func(*args, **kwargs)

            # Include args in key - optionally skip first arg (client/request object)
            func_args_for_key = args[1:] if (skip_first_arg and args) else args

            # Extract user ID if available
            user_id = kwargs.get('user_id', kwargs.get('user_id_for_cache'))
            if not user_id and args and hasattr(args[0], 'user') and hasattr(args[0].user, 'id'):
                user_id = args[0].user.id

            # Build cache key
            final_prefix = f"{key_prefix}_{func.__name__}"
            if user_id:
                final_prefix += f"_user_{user_id}"

            cache_key = generate_cache_key(final_prefix, *func_args_for_key, **kwargs)

            # Check cache
            try:
                cached_result = cache.get(cache_key)
                if cached_result is not None:
                    logger.debug(f"Cache HIT: {cache_key}")
                    return cached_result
            except Exception as e:
                logger.warning(f"Cache get failed for {cache_key}: {e}")

            # Cache miss - execute function
            logger.debug(f"Cache MISS: {cache_key}")
            result = func(*args, **kwargs)

            # Only cache serializable results
            try:
                cache.set(cache_key, result, timeout)
            except Exception as e:
                logger.warning(f"Cache set failed for {cache_key}: {e}. Result not cached.")

            return result

        return wrapper

    return decorator


def convert_image_to_base64(image) -> str:
    """
    Converts and compresses image to base64 string.
    Resizes to max 640x640 and ensures size is under 256KB for Spotify API.
    """
    try:
        # Open image
        img = Image.open(image)
        
        # Convert to RGB if necessary (remove alpha channel)
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
            img = background
        
        # Resize if larger than 640x640
        max_size = (640, 640)
        if img.size[0] > max_size[0] or img.size[1] > max_size[1]:
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
        
        # Save with compression, ensuring size under 256KB
        quality = 95
        while quality > 20:
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=quality, optimize=True)
            size = buffer.tell()
            
            # Spotify requires base64 image < 256KB
            if size < 250000:  # Leave some margin
                buffer.seek(0)
                return base64.b64encode(buffer.read()).decode()
            
            quality -= 10
        
        # If still too large, resize more aggressively
        img.thumbnail((320, 320), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=85, optimize=True)
        buffer.seek(0)
        return base64.b64encode(buffer.read()).decode()
        
    except Exception as e:
        logger.error(f"Error converting image to base64: {e}")
        raise


def convert_str_to_image(image_data: str):
    """Converts base64 string to Django image."""
    decoded_data = base64.b64decode(image_data.encode())
    return SimpleUploadedFile.from_dict({
        "filename": "logo",
        "content": decoded_data,
        "content-type": "image/jpeg",
    })
