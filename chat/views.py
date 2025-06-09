import json
import logging
from django.http import JsonResponse, HttpResponseBadRequest
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET
from django.conf import settings # To get AUTH_USER_MODEL string if needed, though models import it directly
from .models import ChatMessage
# CustomUser can be imported directly if needed for type hinting or specific queries not via settings.AUTH_USER_MODEL
# from users.models import CustomUser

logger = logging.getLogger(__name__)

@login_required
@require_GET
def get_chat_history_api(request, room_id: str):
    """
    API endpoint to fetch paginated chat history for a given room_id.
    Users must be authenticated to access chat history.
    """
    if not room_id:
        return HttpResponseBadRequest(json.dumps({'error': 'Room ID is required.'}), content_type="application/json")

    try:
        # Fetch messages for the room, oldest first for standard chat display
        # select_related('user') to optimize fetching user details (username, profile pic)
        messages_qs = ChatMessage.objects.filter(room_id=room_id).select_related('user').order_by('timestamp')

        page_number = request.GET.get('page', 1)
        # Allow client to specify page size, with a default and a max limit
        try:
            page_size = int(request.GET.get('limit', 30)) # Default to 30 messages per page
            if page_size > 100: # Max page size limit
                page_size = 100
            if page_size <= 0:
                 page_size = 30
        except ValueError:
            page_size = 30 # Default if invalid 'limit' param

        paginator = Paginator(messages_qs, page_size)

        try:
            page_obj = paginator.page(page_number)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            # If page is out of range, deliver last page of results if client asks for page > num_pages
            # Or return empty list if that's preferred for API. Let's return empty for out of range.
            # page_obj = paginator.page(paginator.num_pages)
             return JsonResponse({
                'results': [], 'has_next': False, 'next_page_number': None,
                'has_previous': paginator.num_pages > 0 and int(page_number) > 1,
                'previous_page_number': paginator.num_pages if paginator.num_pages > 0 and int(page_number) > paginator.num_pages else None, # Fix prev page logic for out of range
                'current_page': int(page_number), 'total_pages': paginator.num_pages,
                'count': paginator.count, 'message': 'Page out of range.'
            }, status=200)


        serialized_messages = []
        for message in page_obj.object_list:
            profile_picture_url = None
            # Construct a default static path carefully
            default_pic_path = f"{settings.STATIC_URL}media/profile_pics/default.jpg" # Common default
            if hasattr(message.user, 'profile_picture') and message.user.profile_picture and hasattr(message.user.profile_picture, 'url'):
                try:
                    profile_picture_url = message.user.profile_picture.url
                except ValueError:
                    profile_picture_url = default_pic_path
            else:
                profile_picture_url = default_pic_path


            serialized_messages.append({
                'id': message.id,
                'room_id': message.room_id,
                'user_id': message.user.id,
                'username': message.user.username,
                'profile_picture_url': profile_picture_url,
                'message_text': message.message_text,
                'timestamp': message.timestamp.isoformat(),
            })

        response_data = {
            'results': serialized_messages,
            'has_next': page_obj.has_next(),
            'next_page_number': page_obj.next_page_number() if page_obj.has_next() else None,
            'has_previous': page_obj.has_previous(),
            'previous_page_number': page_obj.previous_page_number() if page_obj.has_previous() else None,
            'current_page': page_obj.number,
            'total_pages': paginator.num_pages,
            'count': paginator.count,
        }
        return JsonResponse(response_data)

    except Exception as e:
        logger.error(f"Error in get_chat_history_api for room {room_id}: {e}", exc_info=True)
        return JsonResponse({'error': 'An unexpected server error occurred.'}, status=500)
