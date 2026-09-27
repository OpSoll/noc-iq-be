"""Encryption utilities for securing webhook custom headers at rest.

Uses Fernet (AES-128-CBC + HMAC-SHA256) with a key derived from the
application SECRET_KEY via PBKDF2. The encrypted payload is stored as
a base64-encoded string in the database so it works across PostgreSQL
and SQLite backends.
"""
import base64
import hashlib
import json
import logging
from typing import Dict, Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

# Module-level cache so we only derive the key once per process.
_fernet: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    """Derive a Fernet key from the application SECRET_KEY."""
    global _fernet
    if _fernet is not None:
        return _fernet

    from app.core.config import settings
    # PBKDF2 with 480k iterations to stretch the SECRET_KEY into 32 bytes
    # suitable for Fernet key derivation.
    raw_key = hashlib.pbkdf2_hmac(
        "sha256",
        settings.SECRET_KEY.encode("utf-8"),
        b"webhook-custom-headers-v1",
        iterations=480_000,
    )
    # Fernet requires a URL-safe base64-encoded 32-byte key
    fernet_key = base64.urlsafe_b64encode(raw_key)
    _fernet = Fernet(fernet_key)
    return _fernet


def encrypt_headers(headers: Optional[Dict[str, str]]) -> Optional[str]:
    """Encrypt a headers dict and return a base64-encoded string for storage.

    Returns None if headers is None or empty.
    """
    if not headers:
        return None
    fernet = _get_fernet()
    plaintext = json.dumps(headers, sort_keys=True).encode("utf-8")
    ciphertext = fernet.encrypt(plaintext)
    return ciphertext.decode("ascii")


def decrypt_headers(encrypted: Optional[str]) -> Optional[Dict[str, str]]:
    """Decrypt a base64-encoded encrypted string back into a headers dict.

    Returns None if encrypted is None or empty.
    Returns an empty dict if decryption fails (logged as warning).
    """
    if not encrypted:
        return None
    fernet = _get_fernet()
    try:
        plaintext = fernet.decrypt(encrypted.encode("ascii"))
        return json.loads(plaintext.decode("utf-8"))
    except (InvalidToken, json.JSONDecodeError, Exception) as exc:
        logger.warning("Failed to decrypt webhook custom headers: %s", exc)
        return {}
