"""
ASGI config for MeloMatch project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os
import django
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'MeloMatch.settings')
django.setup() # Ensure Django is setup before importing routing from apps

# It's important that django.setup() is called before importing parts of your
# application that might depend on Django's settings or ORM being initialized,
# such as routing modules in your apps.

# Import websocket_urlpatterns from your apps AFTER django.setup()
# These imports might fail if the routing files or apps themselves are missing
# due to 'reset_all'. For this subtask, I'm assuming they exist or will be created.
import music.routing # For music app websockets (e.g., collaborative playlists, recommendation notifications)
import users.routing   # For user-specific websockets (e.g., friend activity feed)

application = ProtocolTypeRouter({
    "http": get_asgi_application(), # Standard HTTP handling
    "websocket": AuthMiddlewareStack(
        URLRouter(
            # Combine urlpatterns from different apps
            # Ensure no conflicts in URL patterns across apps
            # (e.g., all music WS URLs start with 'ws/music/', all user WS URLs with 'ws/users/')
            # However, the current patterns are distinct: 'ws/recommendations/' and 'ws/playlist/.../collaborate/' from music,
            # and 'ws/friend_activity/' from users.
            (music.routing.websocket_urlpatterns if hasattr(music.routing, 'websocket_urlpatterns') else []) +
            (users.routing.websocket_urlpatterns if hasattr(users.routing, 'websocket_urlpatterns') else [])
            # Add other app WebSocket routing here if needed
        )
    ),
})

# Note: The original plan for RecommendationConsumer was in music/consumers.py and music/routing.py
# The CollaborativePlaylistConsumer was also in music app.
# FriendActivityConsumer is in users app.
# The paths in the respective routing files are:
# music.routing: re_path(r'ws/recommendations/$', ...) and re_path(r'ws/playlist/(?P<playlist_id>[^/]+)/collaborate/$', ...)
# users.routing: re_path(r'ws/friend_activity/$', ...)
# These paths are distinct and should not clash.
# Adding defensive hasattr checks in case a routing module was lost due to reset.
