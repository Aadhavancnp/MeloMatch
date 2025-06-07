from django.core.cache import cache
import hashlib
import functools
import logging

logger = logging.getLogger(__name__)

def generate_cache_key(prefix, *args, **kwargs):
    """
    Generates a cache key based on a prefix and function arguments.
    Sorts kwargs to ensure consistent key order.
    """
    key_parts = [str(prefix)]
    if args:
        key_parts.extend(str(arg) for arg in args)
    if kwargs:
        # Sort kwargs by key to ensure consistent cache key
        for key, value in sorted(kwargs.items()):
            key_parts.append(f"{key}_{value}")

    # Create a hash for complex/long keys, otherwise join directly
    raw_key = "_".join(key_parts)
    if len(raw_key) > 150: # Max key length can be an issue for some cache backends like memcached
        return f"{prefix}_hash_{hashlib.md5(raw_key.encode('utf-8')).hexdigest()}"
    return raw_key

def cache_api_call(key_prefix, timeout):
    """
    Decorator to cache the results of API call functions.
    `key_prefix` is used to namespace the cache entries.
    `timeout` is the cache timeout in seconds.
    Assumes the decorated function's arguments are suitable for string conversion for the cache key.
    The first argument of the decorated function is often the client instance (e.g., 'sp' for Spotipy),
    which should not be part of the cache key if it contains request-specific auth details that change often.
    For user-specific calls, the cache key must incorporate user identity.
    This decorator is a general version; user-specific keying might need special handling within the key_prefix
    or by passing user_id as an argument to the decorated function.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Exclude the first argument (typically 'sp' client or similar instance) from key generation
            # if it's an object that shouldn't be part of the key directly.
            # This is a simple approach; more complex arg inspection might be needed.

            # If the first arg is an object instance (like 'sp'), we might want to exclude it
            # or use a specific attribute from it for the key.
            # For user-specific Spotify calls, `sp.current_user()['id']` was used in manual keys.
            # This decorator needs to be flexible or make assumptions.
            # For now, let's assume relevant identifiers (like user_id, query, item_id) are passed
            # directly in args or kwargs for key generation.

            # Create a more robust key by filtering out complex objects like 'sp' or 'request'
            key_args = []
            key_kwargs = {}

            # The first argument `args[0]` is often `sp` (spotipy client) or `self` for methods.
            # We need to handle it carefully if it's user-specific.
            # If user-specific data is needed for a cache key, it should be passed explicitly as an arg.

            # Let's make the key based on args[1:] if args[0] seems to be a client instance.
            # This is heuristic. A better way is for the decorated function to accept user_id or similar.

            # Simplified key generation for demonstration:
            # A more robust key generation might inspect arg names or types.

            # For user-specific data, the `key_prefix` itself can be made dynamic by the caller,
            # or the decorated function should receive `user_id` as a kwarg.

            # Let's refine key generation within the wrapper
            # We assume that arguments that make the call unique (query, ids, user_ids) are passed.

            # Example: cache_key = generate_cache_key(f"{key_prefix}_{func.__name__}", *args_for_key, **kwargs_for_key)
            # For simplicity, we'll use all args and kwargs for the key, assuming they are primitive enough.
            # The `generate_cache_key` function above will create a hash if the key is too long.

            # A very common pattern is that the first arg is `sp` or `client` which might be user-specific.
            # If the client object itself (like `sp`) contains user-specific auth that changes frequently,
            # including it directly or its hash in the key might lead to poor caching.
            # The previous manual implementations often used `request.user.id` or `sp.current_user()['id']`.
            # This generic decorator can't easily access `request` or know which arg is `sp` without more rules.

            # Let's assume for now that arguments that define uniqueness (excluding client object itself)
            # are passed and are suitable for key generation.

            # A better generic key:
            func_args_for_key = args[1:] # Skip the client object typically at args[0]

            # If specific user ID is part of kwargs, use it to make key more specific
            user_id_val = kwargs.get('user_id', kwargs.get('user_id_for_cache'))
            if not user_id_val and args and hasattr(args[0], 'user') and hasattr(args[0].user, 'id'): # Check if first arg is request
                 user_id_val = args[0].user.id

            final_key_prefix = f"{key_prefix}_{func.__name__}"
            if user_id_val:
                final_key_prefix += f"_user_{user_id_val}"

            cache_key = generate_cache_key(final_key_prefix, *func_args_for_key, **kwargs)

            cached_result = cache.get(cache_key)
            if cached_result is not None: # Check for None to allow caching of False or 0
                logger.debug(f"Cache HIT for key: {cache_key} in function {func.__name__}")
                return cached_result

            logger.debug(f"Cache MISS for key: {cache_key} in function {func.__name__}. Calling function.")
            result = func(*args, **kwargs)
            cache.set(cache_key, result, timeout)
            return result
        return wrapper
    return decorator

# Example Usage (Illustrative - not to be run here)
# @cache_api_call(key_prefix="my_service_data", timeout=3600)
# def get_data_from_external_api(param1, param2):
#     # ... actual API call ...
#     return {"data": "some_data"}
