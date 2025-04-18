from django.core.cache import caches
from functools import wraps, lru_cache


def tiered_cache(key_prefix=None, timeout=3600, maxsize=None):
    """
    Decorator for implementing local memory caching.
    Can be used with either Django's cache system or Python's lru_cache.

    Args:
        key_prefix: Prefix for Django cache keys
        timeout: Timeout for Django cache entries (in seconds)
        maxsize: Size limit for LRU cache (if using lru_cache)
    """
    # If maxsize is provided, use lru_cache
    if maxsize is not None:
        return lru_cache(maxsize=maxsize)

    # Otherwise use Django's cache system
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Generate a unique cache key
            prefix = key_prefix or func.__name__
            args_hash = hash(str(args))
            kwargs_hash = hash(str(sorted(kwargs.items())))

            cache_key = f"{prefix}:{args_hash}:{kwargs_hash}"

            # Try local memory cache
            local_cache = caches['default']
            result = local_cache.get(cache_key)
            if result is not None:
                return result

            # Execute the function and cache the result
            result = func(*args, **kwargs)
            local_cache.set(cache_key, result, timeout)
            return result

        return wrapper

    return decorator
