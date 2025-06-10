from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/recommendations/$', consumers.RecommendationConsumer.as_asgi()),
    re_path(r'ws/playlist/(?P<playlist_id>[^/]+)/collaborate/$', consumers.CollaborativePlaylistConsumer.as_asgi()),
    # Add other music-related WebSocket consumers here if any
]
