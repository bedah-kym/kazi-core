"""
ASGI config for Backend project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.1/howto/deployment/asgi/
"""

import os
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
from channels.auth import AuthMiddlewareStack
from channels.security.websocket import AllowedHostsOriginValidator

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Backend.settings')

django_asgi_app = get_asgi_application()

import chatbot.routing
import notifications.routing


async def lifespan(scope, receive, send):
    """Handle ASGI lifespan, closing the shared LLM HTTP clients on shutdown.

    Django's ASGI handler serves HTTP only, so without this the lifespan scope
    is unhandled and the reused httpx client is never closed cleanly.
    """
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            from orchestration.llm_client import aclose_llm_clients

            await aclose_llm_clients()
            await send({"type": "lifespan.shutdown.complete"})
            return


application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(URLRouter(
                chatbot.routing.websocket_urlpatterns
                + notifications.routing.websocket_urlpatterns
            ))
        ),
        "lifespan": lifespan,
    }
)
