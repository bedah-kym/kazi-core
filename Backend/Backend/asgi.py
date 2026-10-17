"""
ASGI config for Backend project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.1/howto/deployment/asgi/
"""

import os
from channels.routing import ProtocolTypeRouter, URLRouter
from django.conf import settings
from django.core.asgi import get_asgi_application
from django.core.exceptions import ImproperlyConfigured
from channels.auth import AuthMiddlewareStack
from channels.security.websocket import AllowedHostsOriginValidator

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Backend.settings')

django_asgi_app = get_asgi_application()


def _web_worker_count() -> int:
    try:
        return int(
            os.environ.get('KAZI_WEB_WORKERS')
            or os.environ.get('WEB_CONCURRENCY')
            or os.environ.get('GUNICORN_WORKERS')
            or os.environ.get('UVICORN_WORKERS')
            or '1'
        )
    except ValueError:
        return 1


if getattr(settings, "KAZI_LITE_PROFILE", False) and _web_worker_count() > 1:
    raise ImproperlyConfigured(
        "KAZI_LITE_PROFILE uses in-memory Channels, so it requires exactly "
        "one web worker: room messages would split across processes with "
        f"more ({_web_worker_count()} requested). Start uvicorn with --workers 1."
    )

import chatbot.routing
import notifications.routing

_in_process_scheduler = None


async def lifespan(scope, receive, send):
    """Handle ASGI lifespan: start the lite scheduler, close LLM clients.

    Django's ASGI handler serves HTTP only, so without this the lifespan scope
    is unhandled and the reused httpx client is never closed cleanly.
    """
    global _in_process_scheduler
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            try:
                if getattr(settings, "IN_PROCESS_SCHEDULER", False):
                    from orchestration.in_process_scheduler import InProcessScheduler

                    _in_process_scheduler = InProcessScheduler()
                    _in_process_scheduler.start()
            except Exception as exc:
                await send({"type": "lifespan.startup.failed", "message": str(exc)})
                return
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            if _in_process_scheduler is not None:
                await _in_process_scheduler.stop()
                _in_process_scheduler = None
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
