from django.apps import AppConfig
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


class OrchestrationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'orchestration'

    def _log_default_provider(self):
        """Log which provider answers a chat turn with no room override."""
        try:
            from orchestration.llm_client import get_llm_client

            provider = get_llm_client().default_provider()
            if getattr(settings, "LLM_DEFAULT_PROVIDER", ""):
                logger.info("LLM default provider: %s", provider or "none (no key configured)")
            else:
                logger.info(
                    "LLM default provider: %s (LLM_DEFAULT_PROVIDER unset; "
                    "first configured provider wins)",
                    provider or "none (no key configured)",
                )
        except Exception as exc:
            logger.warning("Could not resolve the LLM default provider: %s", exc)

    def ready(self):
        from orchestration.connector_registry import is_demo_mode

        self._log_default_provider()

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

        from orchestration.shell.profiles import warn_if_open_profile

        warn_if_open_profile()

        if not getattr(settings, "ORCHESTRATION_STRICT_STARTUP_CHECKS", True):
            return
        try:
            from orchestration.tool_router import get_mcp_router

            get_mcp_router()
        except Exception as exc:
            logger.error("Orchestration startup integrity check failed: %s", exc)
            raise
