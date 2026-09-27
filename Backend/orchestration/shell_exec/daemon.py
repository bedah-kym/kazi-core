"""Minimal ASGI sidecar for shell execution.

Served by uvicorn via ``manage.py run_shell_exec``. Two endpoints:

- ``GET /health`` — liveness.
- ``POST /exec`` — bearer-token authenticated; runs one command through the
  configured backend and returns stdout/stderr/exit code.

The sidecar holds no policy and no Kazi credentials — only the shared token and
a workspace directory. All policy lives in Kazi's orchestration layer. See
``docs/contracts/credential-scoping.md``.
"""
from __future__ import annotations

import hmac
import json
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from orchestration.shell_exec.backends import ShellBackend, ShellExecConfig, get_backend

logger = logging.getLogger(__name__)

Message = Dict[str, Any]
Scope = Dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

_MAX_BODY_BYTES = 1_000_000
_TOKEN_HEADER = "x-shell-exec-token"  # nosec B105 - HTTP header name, not a credential


async def _read_body(receive: Receive) -> bytes:
    body = b""
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return body
        body += message.get("body", b"")
        if len(body) > _MAX_BODY_BYTES:
            raise ValueError("request body too large")
        if not message.get("more_body", False):
            return body


async def _send_json(send: Send, status: int, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json")],
    })
    await send({"type": "http.response.body", "body": body})


def _header(scope: Scope, name: str) -> str:
    wanted = name.lower().encode("latin-1")
    for key, value in scope.get("headers", []):
        if key.lower() == wanted:
            return value.decode("latin-1")
    return ""


def _authorized(scope: Scope, token: str) -> bool:
    provided = _header(scope, _TOKEN_HEADER)
    if not token or not provided:
        return False
    return hmac.compare_digest(provided, token)


def create_app(
    config: Optional[ShellExecConfig] = None,
    backend: Optional[ShellBackend] = None,
) -> Callable[[Scope, Receive, Send], Awaitable[None]]:
    """Build the ASGI application. Tests inject ``config`` and ``backend``."""
    config = config or ShellExecConfig.from_settings()
    resolved_backend = backend or get_backend(config.profile, config)

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            return
        path = scope.get("path") or ""
        method = (scope.get("method") or "GET").upper()

        if path == "/health" and method == "GET":
            await _send_json(send, 200, {"status": "ok", "profile": config.profile})
            return
        if path != "/exec":
            await _send_json(send, 404, {"error": "not found"})
            return
        if method != "POST":
            await _send_json(send, 405, {"error": "method not allowed"})
            return
        if not _authorized(scope, config.token):
            await _send_json(send, 401, {"error": "unauthorized"})
            return

        try:
            body = await _read_body(receive)
        except ValueError as exc:
            await _send_json(send, 400, {"error": str(exc)})
            return
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            await _send_json(send, 400, {"error": "invalid json"})
            return
        if not isinstance(payload, dict):
            await _send_json(send, 400, {"error": "request body must be a JSON object"})
            return

        command = payload.get("command")
        if not isinstance(command, str) or not command.strip():
            await _send_json(send, 400, {"error": "command is required"})
            return

        try:
            result = await resolved_backend.execute(
                command,
                room_id=str(payload.get("room_id") or "default"),
                cwd=payload.get("cwd"),
                timeout_s=payload.get("timeout_s"),
                network=str(payload.get("network") or "none"),
            )
        except ValueError as exc:
            await _send_json(send, 400, {"error": str(exc)})
            return
        except Exception as exc:
            logger.error("sidecar exec failed: %s", exc, exc_info=True)
            await _send_json(send, 500, {"error": "execution failed"})
            return

        await _send_json(send, 200, result.as_dict())

    return app
