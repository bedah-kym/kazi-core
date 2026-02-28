"""Unified error handling for connectors."""
from typing import Optional


class ConnectorError(Exception):
    """
    Standardized connector error with retry semantics.

    Attributes:
        message: Human-readable error message
        error_code: Standard error code (e.g., 'RATE_LIMIT', 'AUTH_FAILED', 'SERVICE_ERROR', 'VALIDATION_FAILED')
        retry_after: Seconds to wait before retry (None if non-retryable)
        details: Additional context for debugging
    """

    # Standard error codes
    RATE_LIMIT = "RATE_LIMIT"
    AUTH_FAILED = "AUTH_FAILED"
    SERVICE_ERROR = "SERVICE_ERROR"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT = "TIMEOUT"
    NOT_FOUND = "NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"

    def __init__(
        self,
        message: str,
        error_code: str = "SERVICE_ERROR",
        retry_after: Optional[int] = None,
        details: Optional[dict] = None,
    ):
        self.message = message
        self.error_code = error_code
        self.retry_after = retry_after
        self.details = details or {}
        super().__init__(self.message)

    def is_retryable(self) -> bool:
        """Check if this error should trigger a retry."""
        return self.retry_after is not None

    def to_response(self) -> dict:
        """Convert to API response format."""
        return {
            "status": "error",
            "error_code": self.error_code,
            "message": self.message,
            "retry_after": self.retry_after,
            "details": self.details,
        }

    def __str__(self) -> str:
        if self.retry_after:
            return f"{self.error_code}: {self.message} (retry after {self.retry_after}s)"
        return f"{self.error_code}: {self.message}"
