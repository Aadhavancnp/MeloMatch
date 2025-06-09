from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    # The (?P<room_id>[^/]+) part captures any characters except a slash as room_id
    # For UUIDs: (?P<room_id>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})
    # For simple strings or numbers: (?P<room_id>\w+)
    # Using [^/]+ for flexibility, as room_id could be session_id (hex) or other string.
    re_path(r'ws/chat/(?P<room_id>[^/]+)/$', consumers.ChatConsumer.as_asgi()),
]
