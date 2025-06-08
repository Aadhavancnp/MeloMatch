import logging
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required

from .reporting import (
    get_user_listening_time_trend,
    get_user_mood_distribution,
    get_user_genre_distribution,
    get_music_discovery_insights
)

logger = logging.getLogger(__name__)

@login_required
def api_listening_time_trend(request):
    """
    API endpoint for user listening time trend (proxied by liked songs).
    """
    try:
        # Optional: Get 'days' from request.GET, default to 30
        days = int(request.GET.get('days', 30))
        if not (1 <= days <= 365): # Basic validation
            return JsonResponse({'error': 'Invalid days parameter. Must be between 1 and 365.'}, status=400)

        result = get_user_listening_time_trend(request.user, days=days)
        return JsonResponse({'data': result})
    except ValueError:
        return JsonResponse({'error': 'Invalid days parameter. Must be an integer.'}, status=400)
    except Exception as e:
        logger.error(f"Error in api_listening_time_trend for user {request.user.id}: {e}", exc_info=True)
        return JsonResponse({'error': 'An unexpected error occurred.'}, status=500)

@login_required
def api_mood_distribution(request):
    """
    API endpoint for user mood distribution based on liked songs.
    """
    try:
        result = get_user_mood_distribution(request.user)
        return JsonResponse({'data': result})
    except Exception as e:
        logger.error(f"Error in api_mood_distribution for user {request.user.id}: {e}", exc_info=True)
        return JsonResponse({'error': 'An unexpected error occurred.'}, status=500)

@login_required
def api_genre_distribution(request):
    """
    API endpoint for user genre distribution based on liked songs.
    """
    try:
        # Optional: Get 'top_n' from request.GET, default to 10
        top_n = int(request.GET.get('top_n', 10))
        if not (1 <= top_n <= 50): # Basic validation
             return JsonResponse({'error': 'Invalid top_n parameter. Must be between 1 and 50.'}, status=400)

        result = get_user_genre_distribution(request.user, top_n=top_n)
        return JsonResponse({'data': result})
    except ValueError:
        return JsonResponse({'error': 'Invalid top_n parameter. Must be an integer.'}, status=400)
    except Exception as e:
        logger.error(f"Error in api_genre_distribution for user {request.user.id}: {e}", exc_info=True)
        return JsonResponse({'error': 'An unexpected error occurred.'}, status=500)

@login_required
def api_music_discovery_insights(request):
    """
    API endpoint for music discovery insights (new genres).
    """
    try:
        # Optional: Get 'days' from request.GET, default to 30
        days = int(request.GET.get('days', 30))
        if not (1 <= days <= 365): # Basic validation
            return JsonResponse({'error': 'Invalid days parameter. Must be between 1 and 365.'}, status=400)

        result = get_music_discovery_insights(request.user, days=days)
        return JsonResponse({'data': result})
    except ValueError:
        return JsonResponse({'error': 'Invalid days parameter. Must be an integer.'}, status=400)
    except Exception as e:
        logger.error(f"Error in api_music_discovery_insights for user {request.user.id}: {e}", exc_info=True)
        return JsonResponse({'error': 'An unexpected error occurred.'}, status=500)
