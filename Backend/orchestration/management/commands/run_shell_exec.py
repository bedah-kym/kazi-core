"""Start the shell-exec sidecar (uvicorn)."""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Run the deliberately-dumb shell-exec sidecar (POST /exec, GET /health)."

    def add_arguments(self, parser):
        parser.add_argument("--host", default=None, help="Bind host (default: SHELL_EXEC_HOST).")
        parser.add_argument("--port", type=int, default=None, help="Bind port (default: SHELL_EXEC_PORT).")

    def handle(self, *args, **options):
        from orchestration.shell_exec.backends import ShellExecConfig
        from orchestration.shell_exec.server import serve

        config = ShellExecConfig.from_settings()
        if not config.token:
            raise CommandError(
                "SHELL_EXEC_TOKEN is not set; refusing to start an unauthenticated sidecar."
            )
        host = options.get("host") or config.host
        port = options.get("port") or config.port
        self.stdout.write(
            f"Shell-exec sidecar listening on http://{host}:{port} (profile={config.profile})"
        )
        try:
            serve(config, host=host, port=port)
        except RuntimeError as exc:
            raise CommandError(str(exc))
