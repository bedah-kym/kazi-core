from django.apps import AppConfig
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


class OrchestrationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'orchestration'

    def ready(self):
        if not getattr(settings, "ORCHESTRATION_STRICT_STARTUP_CHECKS", True):
            return
        try:
            from orchestration.mcp_router import get_mcp_router

            get_mcp_router()
        except Exception as exc:
            logger.error("Orchestration startup integrity check failed: %s", exc)
            raise
