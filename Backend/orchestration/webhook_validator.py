"""
Webhook signature verification utilities for external integrations.

This module provides secure webhook signature validation for third-party
integrations like Calendly, WhatsApp, and others.
"""

import hmac
import hashlib
import logging
from typing import Tuple

logger = logging.getLogger(__name__)


def verify_calendly_signature(signature: str, secret: str, body: bytes) -> bool:
    """
    Verify Calendly webhook signature.
    
    Calendly uses HMAC-SHA256 signing.
    
    Args:
        signature: X-Calendly-Signature header value
        secret: Client secret from Calendly
        body: Raw request body
        
    Returns:
        bool: True if signature is valid, False otherwise
    """
    if not signature or not secret:
        logger.warning("Missing signature or secret for Calendly webhook verification")
        return False
    
    try:
        expected_signature = hmac.new(
            secret.encode(),
            body,
            hashlib.sha256
        ).hexdigest()
        
        # Use constant-time comparison to prevent timing attacks
        is_valid = hmac.compare_digest(signature, expected_signature)
        
        if not is_valid:
            logger.warning(f"Invalid Calendly webhook signature")
        
        return is_valid
        
    except Exception as e:
        logger.error(f"Error verifying Calendly signature: {e}")
        return False


def verify_whatsapp_signature(signature: str, token: str, body: str) -> bool:
    """
    Verify WhatsApp webhook signature.
    
    WhatsApp uses HMAC-SHA256 with request body.
    
    Args:
        signature: X-Hub-Signature header value (format: sha1=xxxx)
        token: Verify token from WhatsApp
        body: Raw request body as string
        
    Returns:
        bool: True if signature is valid, False otherwise
    """
    if not signature or not token:
        logger.warning("Missing signature or token for WhatsApp webhook verification")
        return False
    
    try:
        # WhatsApp format: sha1=hex_digest
        if not signature.startswith('sha1='):
            logger.warning("Invalid WhatsApp signature format")
            return False
        
        provided_hash = signature.split('=')[1]
        
        expected_hash = hmac.new(
            token.encode(),
            body.encode() if isinstance(body, str) else body,
            hashlib.sha1
        ).hexdigest()
        
        is_valid = hmac.compare_digest(provided_hash, expected_hash)
        
        if not is_valid:
            logger.warning("Invalid WhatsApp webhook signature")
        
        return is_valid
        
    except Exception as e:
        logger.error(f"Error verifying WhatsApp signature: {e}")
        return False


def verify_generic_hmac_sha256(signature: str, secret: str, body: bytes) -> bool:
    """
    Generic HMAC-SHA256 signature verification.
    
    Suitable for integrations that use standard HMAC-SHA256.
    
    Args:
        signature: Provided signature (hex string)
        secret: Signing secret
        body: Request body bytes
        
    Returns:
        bool: True if signature is valid, False otherwise
    """
    if not signature or not secret:
        logger.warning("Missing signature or secret for HMAC verification")
        return False
    
    try:
        expected_signature = hmac.new(
            secret.encode() if isinstance(secret, str) else secret,
            body,
            hashlib.sha256
        ).hexdigest()
        
        is_valid = hmac.compare_digest(signature, expected_signature)
        
        if not is_valid:
            logger.warning(f"Invalid HMAC-SHA256 signature")
        
        return is_valid
        
    except Exception as e:
        logger.error(f"Error verifying HMAC signature: {e}")
        return False


def log_webhook_verification(
    service: str,
    is_valid: bool,
    user_id: str = None,
    details: dict = None
) -> None:
    """
    Log webhook verification attempts for security auditing.
    
    Args:
        service: Name of the service (e.g., 'calendly', 'whatsapp')
        is_valid: Whether signature was valid
        user_id: Associated user ID if available
        details: Additional details to log
    """
    status = "VALID" if is_valid else "INVALID"
    log_msg = f"Webhook verification [{service}]: {status}"
    if user_id:
        log_msg += f" | user={user_id}"
    if details:
        log_msg += f" | {details}"
    
    if is_valid:
        logger.info(log_msg)
    else:
        logger.warning(log_msg)
