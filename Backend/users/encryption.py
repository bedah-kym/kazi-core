"""
Secure encryption utilities for sensitive data (tokens, API keys, etc.)

This module provides proper encryption/decryption for sensitive data
using Fernet from cryptography library with environment-based key management.
"""

import os
import logging
from pathlib import Path
from django.conf import settings
from cryptography.fernet import Fernet
import base64

logger = logging.getLogger(__name__)


class EncryptionKeyError(Exception):
    """Raised when encryption key is not properly configured."""


class TokenEncryption:
    """
    Secure token encryption/decryption using Fernet with proper key management.

    The encryption key should be:
    1. 32 bytes (256 bits) for AES-128
    2. Loaded from environment variable in production
    3. Never hardcoded or derived from SECRET_KEY
    """

    _cipher = None
    _key = None

    @classmethod
    def get_key(cls):
        """
        Get or initialize the encryption key.

        In production: key must be set via ENCRYPTION_KEY environment variable
        In development: key is generated once and persisted to a gitignored file

        Returns:
            bytes: The encryption key

        Raises:
            EncryptionKeyError: If key cannot be obtained in production
        """
        if cls._key is not None:
            return cls._key

        # Try to get from environment first
        key_string = os.environ.get('ENCRYPTION_KEY')

        if key_string:
            cls._key = cls._validate_key(key_string)
            return cls._key

        # In production without key, fail loudly
        if not settings.DEBUG:
            raise EncryptionKeyError(
                "CRITICAL: ENCRYPTION_KEY environment variable not set. "
                "Generate a key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )

        # Development: load or create a stable per-checkout key persisted to a
        # gitignored file so room encryption keys survive restarts. Mirrors the
        # DJANGO_SECRET_KEY fallback (.dev_secret_key) in settings.py.
        cls._key = cls._load_or_create_dev_key()
        return cls._key

    @classmethod
    def _validate_key(cls, key_string):
        try:
            key_bytes = key_string.strip().encode('utf-8')
            decoded = base64.urlsafe_b64decode(key_bytes)
        except Exception as e:
            raise EncryptionKeyError(f"Invalid encryption key format: {e}")
        if len(decoded) != 32:
            raise EncryptionKeyError(f"Encryption key must be 32 bytes, got {len(decoded)}")
        return key_bytes

    @classmethod
    def _load_or_create_dev_key(cls, key_file=None):
        if key_file is None:
            key_file = Path(settings.BASE_DIR) / '.encryption.key'
        try:
            key = key_file.read_text().strip()
        except OSError:
            key = ''
        if key:
            try:
                return cls._validate_key(key)
            except EncryptionKeyError:
                logger.warning("Stored encryption key at %s is invalid; regenerating.", key_file)
        key = Fernet.generate_key()
        try:
            key_file.write_text(key.decode('utf-8'))
            logger.warning(
                "Generated and persisted a new encryption key to %s. "
                "Keep this file; losing it makes existing rooms undecryptable.",
                key_file,
            )
        except OSError as e:
            logger.warning(
                "Could not persist encryption key to %s (%s); using an in-memory key.",
                key_file, e,
            )
        return key

    @classmethod
    def get_cipher(cls):
        """Get or initialize the Fernet cipher."""
        if cls._cipher is None:
            key = cls.get_key()
            cls._cipher = Fernet(key)
        return cls._cipher

    @classmethod
    def encrypt(cls, plaintext: str) -> str:
        """
        Encrypt a plaintext string.

        Args:
            plaintext: String to encrypt

        Returns:
            str: Base64-encoded encrypted token
        """
        if not isinstance(plaintext, str):
            plaintext = str(plaintext)

        cipher = cls.get_cipher()
        encrypted = cipher.encrypt(plaintext.encode('utf-8'))
        return encrypted.decode('utf-8')

    @classmethod
    def decrypt(cls, ciphertext: str) -> str:
        """
        Decrypt a ciphertext string.

        Args:
            ciphertext: Encrypted string to decrypt

        Returns:
            str: Decrypted plaintext

        Raises:
            cryptography.fernet.InvalidToken: If ciphertext is invalid or tampered
        """
        if not ciphertext:
            return None

        try:
            cipher = cls.get_cipher()
            decrypted = cipher.decrypt(ciphertext.encode('utf-8'))
            return decrypted.decode('utf-8')
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise

    @classmethod
    def safe_decrypt(cls, ciphertext: str, default=None):
        """
        Decrypt with error handling - returns default if decryption fails.

        Args:
            ciphertext: Encrypted string to decrypt
            default: Value to return if decryption fails

        Returns:
            str or default: Decrypted plaintext or default value
        """
        try:
            return cls.decrypt(ciphertext)
        except Exception as e:
            logger.warning(f"Safe decryption failed (returning default): {e}")
            return default


def generate_encryption_key() -> str:
    """
    Generate a new encryption key for configuration.

    Returns:
        str: Base64-encoded encryption key
    """
    key = Fernet.generate_key()
    return key.decode('utf-8')
