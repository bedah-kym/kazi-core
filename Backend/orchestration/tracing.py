"""Distributed tracing support with correlation IDs."""
import contextvars
import uuid
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Thread-local context for correlation IDs
_correlation_id_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "correlation_id", default=None
)

_request_context: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "request_context", default=None
)


def generate_correlation_id() -> str:
    """Generate a unique correlation ID."""
    return str(uuid.uuid4())


def set_correlation_id(correlation_id: str) -> None:
    """Set the correlation ID for the current context."""
    _correlation_id_context.set(correlation_id)


def get_correlation_id() -> str:
    """Get the current correlation ID or generate a new one."""
    correlation_id = _correlation_id_context.get()
    if not correlation_id:
        correlation_id = generate_correlation_id()
        _correlation_id_context.set(correlation_id)
    return correlation_id


def set_request_context(context: Dict[str, Any]) -> None:
    """Set request context (user_id, room_id, etc.) for the current operation."""
    _request_context.set(context)


def get_request_context() -> Optional[Dict[str, Any]]:
    """Get the current request context."""
    return _request_context.get()


class TraceLogger:
    """Helper for trace-aware logging."""

    @staticmethod
    def log(level: int, message: str, **extra):
        """Log a message with correlation ID attached."""
        correlation_id = get_correlation_id()
        context = get_request_context() or {}

        # Add context to log record
        extra_context = {
            "correlation_id": correlation_id,
            "user_id": context.get("user_id"),
            "room_id": context.get("room_id"),
        }
        extra_context.update(extra)

        # Use logger.log with extra parameter
        logger.log(level, message, extra=extra_context)

    @staticmethod
    def info(message: str, **extra):
        TraceLogger.log(logging.INFO, message, **extra)

    @staticmethod
    def warning(message: str, **extra):
        TraceLogger.log(logging.WARNING, message, **extra)

    @staticmethod
    def error(message: str, **extra):
        TraceLogger.log(logging.ERROR, message, **extra)

    @staticmethod
    def debug(message: str, **extra):
        TraceLogger.log(logging.DEBUG, message, **extra)
