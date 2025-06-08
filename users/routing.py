from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/friend_activity/$', consumers.FriendActivityConsumer.as_asgi()),
    # Add other user-specific WebSocket consumers here if any
]
