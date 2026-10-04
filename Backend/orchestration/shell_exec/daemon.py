"""Minimal ASGI sidecar for shell execution.

Served by uvicorn via ``manage.py run_shell_exec``. Two endpoints:

- ``GET /health`` — liveness.
- ``POST /exec`` — bearer-token authenticated; runs one command through the
  backend for the requested profile and returns stdout/stderr/exit code.

The sidecar holds no policy and no Kazi credentials — only the shared token and
a workspace directory. All policy lives in Kazi's orchestration layer. See
``docs/contracts/credential-scoping.md``.
"""
from __future__ import annotations

import hmac
import json
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from orchestration.shell_exec import squid
from orchestration.shell_exec.backends import (
    ShellBackend,
    ShellExecConfig,
    get_backend,
    restore_snapshot,
    snapshot_workspace,
)

logger = logging.getLogger(__name__)

Message = Dict[str, Any]
Scope = Dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

_MAX_BODY_BYTES = 1_000_000
_MAX_ALLOWED_HOSTS = 200
_TOKEN_HEADER = "x-shell-exec-token"  # nosec B105 - HTTP header name, not a credential


async def _read_body(receive: Receive) -> bytes:
    """Read the full request body, rejecting oversized payloads."""
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
    """Send a JSON HTTP response."""
    body = json.dumps(payload).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json")],
    })
    await send({"type": "http.response.body", "body": body})


def _header(scope: Scope, name: str) -> str:
    """Return a header value by case-insensitive name, or ``""``."""
    wanted = name.lower().encode("latin-1")
    for key, value in scope.get("headers", []):
        if key.lower() == wanted:
            return value.decode("latin-1")
    return ""


def _authorized(scope: Scope, token: str) -> bool:
    """Constant-time token check. Compares bytes so a non-ASCII header value
    cannot raise inside ``hmac.compare_digest``."""
    provided = _header(scope, _TOKEN_HEADER)
    if not token or not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), token.encode("utf-8"))


def create_app(
    config: Optional[ShellExecConfig] = None,
    backend: Optional[ShellBackend] = None,
) -> Callable[[Scope, Receive, Send], Awaitable[None]]:
    """Build the ASGI application. Tests inject ``config`` and ``backend``."""
    config = config or ShellExecConfig.from_settings()

    def resolve_backend(profile: str) -> ShellBackend:
        if backend is not None:
            return backend
        return get_backend(profile, config)

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            return
        path = scope.get("path") or ""
        method = (scope.get("method") or "GET").upper()

        if path == "/health" and method == "GET":
            await _send_json(send, 200, {"status": "ok", "profile": config.profile})
            return
        if path == "/rollback":
            if method != "POST":
                await _send_json(send, 405, {"error": "method not allowed"})
                return
            if not _authorized(scope, config.token):
                await _send_json(send, 401, {"error": "unauthorized"})
                return
            try:
                payload = json.loads(await _read_body(receive) or b"{}")
            except ValueError:
                await _send_json(send, 400, {"error": "invalid request body"})
                return
            if not isinstance(payload, dict) or not payload.get("snapshot"):
                await _send_json(send, 400, {"error": "snapshot is required"})
                return
            try:
                restore_snapshot(
                    config,
                    str(payload.get("room_id") or "default"),
                    str(payload["snapshot"]),
                )
            except ValueError as exc:
                await _send_json(send, 400, {"error": str(exc)})
                return
            except Exception:
                logger.error("rollback failed", exc_info=True)
                await _send_json(send, 500, {"error": "rollback failed"})
                return
            await _send_json(send, 200, {"status": "restored", "snapshot": str(payload["snapshot"])})
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

        profile = str(payload.get("profile") or config.profile or "standard")
        if profile not in config.allowed_profiles:
            await _send_json(send, 400, {"error": f"profile {profile!r} is not enabled on this sidecar"})
            return

        # Network is off unless Kazi's gate explicitly enabled it per command.
        network = str(payload.get("network") or "none")
        if network not in ("none", "bridge", "proxy"):
            await _send_json(send, 400, {"error": f"unsupported network mode {network!r}"})
            return
        extra: Dict[str, Any] = {}
        if network == "proxy":
            if not config.egress_proxy:
                await _send_json(send, 400, {"error": "the egress proxy is not enabled on this sidecar"})
                return
            hosts = payload.get("allowed_hosts") or []
            if (
                not isinstance(hosts, list)
                or len(hosts) > _MAX_ALLOWED_HOSTS
                or not all(squid.valid_host_entry(host) for host in hosts)
            ):
                await _send_json(send, 400, {"error": "allowed_hosts must be a list of host names"})
                return
            extra["allowed_hosts"] = hosts

        # Snapshot the workspace before a destructive command (Kazi decides when).
        snapshot_id = None
        if payload.get("snapshot"):
            try:
                snapshot_id = snapshot_workspace(config, str(payload.get("room_id") or "default"))
            except Exception:
                logger.warning("workspace snapshot failed", exc_info=True)

        try:
            result = await resolve_backend(profile).execute(
                command,
                room_id=str(payload.get("room_id") or "default"),
                cwd=payload.get("cwd"),
                timeout_s=payload.get("timeout_s"),
                network=network,
                **extra,
            )
        except ValueError as exc:
            await _send_json(send, 400, {"error": str(exc)})
            return
        except Exception as exc:
            logger.error("sidecar exec failed: %s", exc, exc_info=True)
            await _send_json(send, 500, {"error": "execution failed"})
            return

        response = result.as_dict()
        if snapshot_id:
            response["snapshot"] = snapshot_id
        await _send_json(send, 200, response)

    return app
