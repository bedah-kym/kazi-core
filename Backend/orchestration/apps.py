from django.apps import AppConfig
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


class OrchestrationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'orchestration'

    def ready(self):
        from orchestration.connector_registry import is_demo_mode

        if is_demo_mode():
            banner = (
                "================================================================\n"
                "  KAZI_DEMO_MODE=true  —  example connectors enabled, no real\n"
                "  credentials required. DO NOT use this configuration in\n"
                "  production. Set KAZI_DEMO_MODE=false (or unset it) to boot\n"
                "  with the normal connector set.\n"
                "================================================================"
            )
            logger.warning(banner)

        if not getattr(settings, "ORCHESTRATION_STRICT_STARTUP_CHECKS", True):
            return
        try:
            from orchestration.tool_router import get_mcp_router

            get_mcp_router()
        except Exception as exc:
            logger.error("Orchestration startup integrity check failed: %s", exc)
            raise
