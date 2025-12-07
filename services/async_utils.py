"""
Async utilities for MeloMatch services.

Provides async-compatible decorators, helpers, and wrappers for:
- Caching with Django cache backend
- ORM operations
- Running sync code in thread executors
- Parallel API calls with asyncio.gather()
"""
import asyncio
import functools
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, List, Optional, TypeVar, ParamSpec

from asgiref.sync import sync_to_async
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Type variables for generic typing
T = TypeVar('T')
P = ParamSpec('P')

# Thread pool for running sync code - increased for better parallelism
_executor = ThreadPoolExecutor(max_workers=20)


def generate_cache_key(prefix: str, *args, **kwargs) -> str:
    """Generates a cache key based on prefix and function arguments."""
    key_parts = [str(prefix)]
    key_parts.extend(str(arg) for arg in args)
    
    for key, value in sorted(kwargs.items()):
        key_parts.append(f"{key}_{value}")
    
    raw_key = "_".join(key_parts)
    if len(raw_key) > 150:
        return f"{prefix}_hash_{hashlib.md5(raw_key.encode('utf-8')).hexdigest()}"
    return raw_key


# Async-compatible cache operations
async_cache_get = sync_to_async(cache.get, thread_sensitive=False)
async_cache_set = sync_to_async(cache.set, thread_sensitive=False)
async_cache_delete = sync_to_async(cache.delete, thread_sensitive=False)


def async_cache_api_call(key_prefix: str, timeout: int = 3600, exclude_functions: Optional[List[str]] = None, skip_first_arg: bool = True):
    """
    Async decorator to cache API call results with specified timeout.
    Works with both async and sync functions.
    
    Args:
        key_prefix: Prefix for the cache key
        timeout: Cache timeout in seconds (default: 1 hour)
        exclude_functions: List of function names that should not be cached
        skip_first_arg: If True, skip first arg (typically client/request object).
                        Set to False for functions where first arg is important data.
    """
    if exclude_functions is None:
        exclude_functions = ['get_spotify_client']
    
    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs) -> T:
            # Skip caching for excluded functions
            if func.__name__ in exclude_functions:
                logger.debug(f"Skipping cache for excluded function: {func.__name__}")
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                return await run_in_executor(func, *args, **kwargs)
            
            # Build cache key - optionally skip first arg (client/request object)
            func_args_for_key = args[1:] if (skip_first_arg and args) else args
            
            # Extract user ID if available
            user_id = kwargs.get('user_id', kwargs.get('user_id_for_cache'))
            if not user_id and args and hasattr(args[0], 'user') and hasattr(args[0].user, 'id'):
                user_id = args[0].user.id
            
            final_prefix = f"{key_prefix}_{func.__name__}"
            if user_id:
                final_prefix += f"_user_{user_id}"
            
            cache_key = generate_cache_key(final_prefix, *func_args_for_key, **kwargs)
            
            # Check cache
            try:
                cached_result = await async_cache_get(cache_key)
                if cached_result is not None:
                    logger.debug(f"Cache HIT: {cache_key}")
                    return cached_result
            except Exception as e:
                logger.warning(f"Cache get failed for {cache_key}: {e}")
            
            # Cache miss - execute function
            logger.debug(f"Cache MISS: {cache_key}")
            
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = await run_in_executor(func, *args, **kwargs)
            
            # Cache the result
            try:
                await async_cache_set(cache_key, result, timeout)
            except Exception as e:
                logger.warning(f"Cache set failed for {cache_key}: {e}")
            
            return result
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs) -> T:
            """Fallback sync wrapper for backwards compatibility."""
            # Skip caching for excluded functions
            if func.__name__ in exclude_functions:
                logger.debug(f"Skipping cache for excluded function: {func.__name__}")
                return func(*args, **kwargs)
            
            func_args_for_key = args[1:] if args else []
            user_id = kwargs.get('user_id', kwargs.get('user_id_for_cache'))
            if not user_id and args and hasattr(args[0], 'user') and hasattr(args[0].user, 'id'):
                user_id = args[0].user.id
            
            final_prefix = f"{key_prefix}_{func.__name__}"
            if user_id:
                final_prefix += f"_user_{user_id}"
            
            cache_key = generate_cache_key(final_prefix, *func_args_for_key, **kwargs)
            
            try:
                cached_result = cache.get(cache_key)
                if cached_result is not None:
                    logger.debug(f"Cache HIT: {cache_key}")
                    return cached_result
            except Exception as e:
                logger.warning(f"Cache get failed for {cache_key}: {e}")
            
            logger.debug(f"Cache MISS: {cache_key}")
            result = func(*args, **kwargs)
            
            try:
                cache.set(cache_key, result, timeout)
            except Exception as e:
                logger.warning(f"Cache set failed for {cache_key}: {e}")
            
            return result
        
        # Return appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        
        # For sync functions, we return the sync wrapper but also attach async version
        sync_wrapper.async_version = async_wrapper
        return sync_wrapper
    
    return decorator


async def run_in_executor(func: Callable[P, T], *args, **kwargs) -> T:
    """
    Run a synchronous function in a thread executor.
    
    This is useful for running blocking I/O operations (like spotipy calls)
    from async views without blocking the event loop.
    
    Args:
        func: The synchronous function to run
        *args: Positional arguments to pass to the function
        **kwargs: Keyword arguments to pass to the function
    
    Returns:
        The result of the function call
    """
    loop = asyncio.get_event_loop()
    
    if kwargs:
        # functools.partial handles kwargs
        func_with_args = functools.partial(func, *args, **kwargs)
        return await loop.run_in_executor(_executor, func_with_args)
    else:
        return await loop.run_in_executor(_executor, func, *args)


async def gather_with_exceptions(*coros, return_exceptions: bool = True) -> List[Any]:
    """
    Run multiple coroutines concurrently and handle exceptions gracefully.
    
    Args:
        *coros: Coroutines to run concurrently
        return_exceptions: If True, exceptions are returned as results.
                          If False, first exception is raised.
    
    Returns:
        List of results (or exceptions if return_exceptions=True)
    """
    results = await asyncio.gather(*coros, return_exceptions=return_exceptions)
    
    if return_exceptions:
        # Log any exceptions that occurred
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Concurrent task {i} failed: {result}")
    
    return results


# =============================================================================
# ORM ASYNC HELPERS
# =============================================================================

def async_get_object_or_404(model, *args, **kwargs):
    """
    Async version of get_object_or_404.
    
    Usage:
        track = await async_get_object_or_404(Track, spotify_id=track_id)
    """
    from django.shortcuts import get_object_or_404
    return sync_to_async(get_object_or_404, thread_sensitive=True)(model, *args, **kwargs)


def make_async_queryset_method(method_name: str):
    """
    Factory to create async versions of queryset methods.
    
    Usage:
        async_filter = make_async_queryset_method('filter')
        tracks = await async_filter(Track.objects, spotify_id__in=ids)
    """
    async def async_method(queryset, *args, **kwargs):
        method = getattr(queryset, method_name)
        return await sync_to_async(lambda: list(method(*args, **kwargs)), thread_sensitive=True)()
    return async_method


# Pre-built async queryset helpers
async def async_queryset_list(queryset) -> List:
    """Convert a queryset to a list asynchronously."""
    return await sync_to_async(list, thread_sensitive=True)(queryset)


async def async_queryset_first(queryset):
    """Get first item from queryset asynchronously."""
    return await sync_to_async(queryset.first, thread_sensitive=True)()


async def async_queryset_get(queryset, *args, **kwargs):
    """Get single item from queryset asynchronously."""
    return await sync_to_async(queryset.get, thread_sensitive=True)(*args, **kwargs)


async def async_queryset_filter(queryset, *args, **kwargs):
    """Filter queryset asynchronously and return list."""
    filtered = queryset.filter(*args, **kwargs)
    return await sync_to_async(list, thread_sensitive=True)(filtered)


async def async_queryset_create(model, **kwargs):
    """Create model instance asynchronously."""
    return await sync_to_async(model.objects.create, thread_sensitive=True)(**kwargs)


async def async_model_save(instance, **kwargs):
    """Save model instance asynchronously."""
    return await sync_to_async(instance.save, thread_sensitive=True)(**kwargs)


async def async_model_delete(instance):
    """Delete model instance asynchronously."""
    return await sync_to_async(instance.delete, thread_sensitive=True)()


# =============================================================================
# CELERY ASYNC HELPERS
# =============================================================================

async def async_celery_delay(task, *args, **kwargs):
    """
    Schedule a Celery task from an async view.
    
    Usage:
        await async_celery_delay(extract_track_features_task, track_id, preview_url)
    """
    return await sync_to_async(task.delay, thread_sensitive=False)(*args, **kwargs)


# =============================================================================
# HTTP CLIENT FOR ASYNC REQUESTS
# =============================================================================

class AsyncHTTPClient:
    """
    Async HTTP client wrapper using httpx.
    
    Provides a simple interface for making async HTTP requests with
    automatic retry, timeout, and error handling.
    """
    
    def __init__(self, base_url: str = "", timeout: float = 10.0, max_retries: int = 3):
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = None
    
    async def __aenter__(self):
        import httpx
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()
    
    async def get(self, url: str, params: dict = None, **kwargs) -> dict:
        """Make an async GET request."""
        import httpx
        
        for attempt in range(self.max_retries):
            try:
                response = await self._client.get(url, params=params, **kwargs)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error on attempt {attempt + 1}: {e}")
                if attempt == self.max_retries - 1:
                    raise
            except httpx.RequestError as e:
                logger.error(f"Request error on attempt {attempt + 1}: {e}")
                if attempt == self.max_retries - 1:
                    raise
            
            # Exponential backoff
            await asyncio.sleep(2 ** attempt)
        
        return {}
    
    async def post(self, url: str, data: dict = None, json: dict = None, **kwargs) -> dict:
        """Make an async POST request."""
        import httpx
        
        for attempt in range(self.max_retries):
            try:
                response = await self._client.post(url, data=data, json=json, **kwargs)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error on attempt {attempt + 1}: {e}")
                if attempt == self.max_retries - 1:
                    raise
            except httpx.RequestError as e:
                logger.error(f"Request error on attempt {attempt + 1}: {e}")
                if attempt == self.max_retries - 1:
                    raise
            
            await asyncio.sleep(2 ** attempt)
        
        return {}
