import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "design_workflow_backend.settings")
django_asgi_application = get_asgi_application()

# These modules access Django models and must load after the app registry is ready.
from ws.jwt_middleware import SimpleJwtTokenAuthMiddleware  # noqa: E402
from ws.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_application,
        "websocket": SimpleJwtTokenAuthMiddleware(URLRouter(websocket_urlpatterns)),
    }
)
