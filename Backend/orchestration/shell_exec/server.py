"""Start the shell-exec sidecar without Django.

``python -m orchestration.shell_exec`` runs the same ASGI app as
``manage.py run_shell_exec``, but does not call ``django.setup()`` and never
imports ``django.conf``. The sidecar holds no Django state: it reads its config
from the environment and serves the daemon in :mod:`orchestration.shell_exec.daemon`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from orchestration.shell_exec.backends import ShellExecConfig, prepare_egress
from orchestration.shell_exec.daemon import create_app

_REPO_ROOT = Path(__file__).resolve().parents[3]


def load_environment() -> None:
    """Load the repo-root ``.env`` exactly as ``Backend.settings`` does."""
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    env_path = _REPO_ROOT / ".env"
    load_dotenv(dotenv_path=env_path, override=True)
    try:
        load_dotenv(str(env_path))
    except Exception:
        pass


def serve(
    config: ShellExecConfig,
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> None:
    """Validate the config and run the sidecar until interrupted."""
    if not config.token:
        raise RuntimeError(
            "SHELL_EXEC_TOKEN is not set; refusing to start an unauthenticated sidecar."
        )

    if config.egress_proxy:
        import asyncio

        error = asyncio.run(prepare_egress(config))
        if error:
            raise RuntimeError(
                "SHELL_EGRESS_PROXY is on but the egress proxy cannot be used: "
                f"{error}"
            )

    import uvicorn

    uvicorn.run(
        create_app(config),
        host=host or config.host,
        port=port or config.port,
        log_level="info",
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m orchestration.shell_exec",
        description="Run the shell-exec sidecar without a Django process.",
    )
    parser.add_argument("--host", default=None, help="Bind host (default: SHELL_EXEC_HOST).")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default: SHELL_EXEC_PORT).")
    args = parser.parse_args(argv)

    load_environment()
    config = ShellExecConfig.from_env()
    try:
        serve(config, host=args.host, port=args.port)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
