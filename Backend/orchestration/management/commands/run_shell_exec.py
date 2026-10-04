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
        from orchestration.shell_exec.daemon import create_app

        import uvicorn

        config = ShellExecConfig.from_settings()
        if not config.token:
            raise CommandError(
                "SHELL_EXEC_TOKEN is not set; refusing to start an unauthenticated sidecar."
            )

        if config.egress_proxy:
            # Check the engine, build the proxy image and sweep leftovers now,
            # so no request ever waits on a build or inherits a leaked network.
            import asyncio

            from orchestration.shell_exec.backends import prepare_egress

            error = asyncio.run(prepare_egress(config))
            if error:
                raise CommandError(f"SHELL_EGRESS_PROXY is on but the egress proxy cannot be used: {error}")
            self.stdout.write("Egress proxy ready (stock Squid, one per sandboxed network command).")

        host = options.get("host") or config.host
        port = options.get("port") or config.port
        app = create_app(config)
        self.stdout.write(
            f"Shell-exec sidecar listening on http://{host}:{port} (profile={config.profile})"
        )
        uvicorn.run(app, host=host, port=port, log_level="info")
