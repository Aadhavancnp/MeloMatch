import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
# from channels.auth import AuthMiddlewareStack # Uncomment if/when auth is needed for WebSockets
# import music.routing # Example: will create app-specific routing later if needed

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'MeloMatch.settings')

# Get the default Django HTTP application first
http_application = get_asgi_application()

from channels.auth import AuthMiddlewareStack # Now needed
import music.routing # Import your app's routing

application = ProtocolTypeRouter({
    "http": http_application,
    "websocket": AuthMiddlewareStack(
        URLRouter(
            music.routing.websocket_urlpatterns
        )
    ),
})
