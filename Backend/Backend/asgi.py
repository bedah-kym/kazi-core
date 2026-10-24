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
    """Read the first nonempty worker count from the supported environment keys.

    Precedence is KAZI_WEB_WORKERS, WEB_CONCURRENCY, GUNICORN_WORKERS, then
    UVICORN_WORKERS. Return 1 if none is set or the selected value is invalid.
    """
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
    """Start the scheduler when IN_PROCESS_SCHEDULER is enabled.

    Django's ASGI handler serves HTTP only, so without this the lifespan scope
    is unhandled and the reused httpx client is never closed cleanly.
    Scheduler startup errors produce a lifespan.startup.failed response.
    Shutdown stops the scheduler and closes the shared LLM HTTP clients.
    """
    global _in_process_scheduler
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            try:
                if getattr(settings, "KAZI_LITE_PROFILE", False):
                    # Lite is guaranteed one web worker, so anything a previous
                    # boot left running or queued is provably abandoned here.
                    from channels.db import database_sync_to_async

                    from orchestration.turns import reap_boot_rows

                    await database_sync_to_async(reap_boot_rows)()
                if getattr(settings, "IN_PROCESS_SCHEDULER", False):
                    from orchestration.in_process_scheduler import InProcessScheduler

                    _in_process_scheduler = InProcessScheduler()
                    _in_process_scheduler.start()
            except Exception as exc:
                await send({"type": "lifespan.startup.failed", "message": str(exc)})
                return
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            from orchestration.turn_leases import (
                shutdown_grace_seconds,
                stop_heartbeat,
                wait_for_idle,
            )

            try:
                await wait_for_idle(shutdown_grace_seconds())
            finally:
                await stop_heartbeat()
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
