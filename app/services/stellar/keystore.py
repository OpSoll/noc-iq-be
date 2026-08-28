"""Encryption at rest for Stellar operator wallet secret keys.

An operator secret key sitting in plaintext in an env var or config file is
a single ``cat`` away from draining the settlement wallet. This module keeps
the key encrypted wherever it is stored and decrypts it only inside the
process, for the duration of a signing call — :func:`signing_key` hands the
plaintext to a callback and wipes the buffer afterwards, so it is never
assigned to a long-lived attribute or returned to the caller.

Two schemes are supported, selected by ``STELLAR_KEY_ENCRYPTION_SCHEME``:

  * ``fernet`` (default) — ``cryptography.fernet.Fernet``: AES-128-CBC with
    an HMAC-SHA256 authentication tag.
  * ``aesgcm`` — AES-256-GCM (authenticated encryption with a 256-bit key).

Ciphertexts are self-describing (``stellar.v1.<scheme>.<payload>``), so a
token stays readable after the configured scheme changes and keys can be
migrated one at a time.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
from contextlib import contextmanager
from typing import Callable, Iterator, Optional, TypeVar

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import Settings, settings as app_settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

TOKEN_PREFIX = "stellar.v1"
SCHEME_FERNET = "fernet"
SCHEME_AESGCM = "aesgcm"
SUPPORTED_SCHEMES = (SCHEME_FERNET, SCHEME_AESGCM)

# PBKDF2 parameters used when no dedicated STELLAR_KEY_ENCRYPTION_KEY is set.
_KDF_SALT = b"stellar-secret-key-encryption-v1"
_KDF_ITERATIONS = 480_000
_KEY_BYTES = 32          # 256-bit: AES-256 for aesgcm, Fernet's full key
_AESGCM_NONCE_BYTES = 12

# Stellar ed25519 secret seeds: 'S' followed by 55 base32 characters.
_SECRET_KEY_RE = re.compile(r"^S[A-Z2-7]{55}$")

# Derived keys are cached per (scheme, secret) so the 480k-iteration KDF
# runs once per process rather than once per signature.
_key_cache: dict[tuple[str, str], bytes] = {}


class SecretKeyEncryptionError(RuntimeError):
    """Raised when a secret key cannot be encrypted or decrypted.

    Messages never include key material — only the reason code.
    """

    REASON_INVALID_SECRET = "INVALID_STELLAR_SECRET_KEY"
    REASON_UNSUPPORTED_SCHEME = "UNSUPPORTED_ENCRYPTION_SCHEME"
    REASON_MALFORMED_TOKEN = "MALFORMED_CIPHERTEXT"
    REASON_DECRYPTION_FAILED = "DECRYPTION_FAILED"
    REASON_NOT_CONFIGURED = "OPERATOR_SECRET_NOT_CONFIGURED"

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"[{reason}] {detail}")


def is_valid_secret_key(secret: str) -> bool:
    """Return True if *secret* looks like a Stellar ed25519 secret seed."""
    return bool(_SECRET_KEY_RE.match((secret or "").strip()))


def _resolve_scheme(scheme: Optional[str], settings: Settings) -> str:
    resolved = (scheme or settings.STELLAR_KEY_ENCRYPTION_SCHEME or SCHEME_FERNET).lower()
    if resolved not in SUPPORTED_SCHEMES:
        raise SecretKeyEncryptionError(
            SecretKeyEncryptionError.REASON_UNSUPPORTED_SCHEME,
            f"{resolved!r} is not a supported scheme; expected one of "
            f"{list(SUPPORTED_SCHEMES)}.",
        )
    return resolved


def _derive_key(scheme: str, settings: Settings) -> bytes:
    """Return the 32-byte data key for *scheme*.

    Uses ``STELLAR_KEY_ENCRYPTION_KEY`` when configured (a url-safe base64
    32-byte key), otherwise stretches ``SECRET_KEY`` with PBKDF2-HMAC-SHA256.
    """
    configured = (settings.STELLAR_KEY_ENCRYPTION_KEY or "").strip()
    cache_key = (scheme, configured or settings.SECRET_KEY)
    cached = _key_cache.get(cache_key)
    if cached is not None:
        return cached

    if configured:
        try:
            raw = base64.urlsafe_b64decode(configured.encode("ascii"))
        except Exception as exc:  # noqa: BLE001 - surfaced as a typed error
            raise SecretKeyEncryptionError(
                SecretKeyEncryptionError.REASON_UNSUPPORTED_SCHEME,
                "STELLAR_KEY_ENCRYPTION_KEY must be url-safe base64.",
            ) from exc
        if len(raw) != _KEY_BYTES:
            raise SecretKeyEncryptionError(
                SecretKeyEncryptionError.REASON_UNSUPPORTED_SCHEME,
                f"STELLAR_KEY_ENCRYPTION_KEY must decode to {_KEY_BYTES} bytes "
                f"(got {len(raw)}).",
            )
    else:
        raw = hashlib.pbkdf2_hmac(
            "sha256",
            settings.SECRET_KEY.encode("utf-8"),
            _KDF_SALT,
            iterations=_KDF_ITERATIONS,
            dklen=_KEY_BYTES,
        )

    _key_cache[cache_key] = raw
    return raw


def reset_key_cache() -> None:
    """Drop cached data keys — call after rotating SECRET_KEY in-process."""
    _key_cache.clear()


def encrypt_secret_key(
    secret: str,
    scheme: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> str:
    """Encrypt a Stellar secret key for storage.

    Returns a self-describing ``stellar.v1.<scheme>.<payload>`` token.

    Raises:
        SecretKeyEncryptionError: if *secret* is not a well-formed Stellar
            secret seed, or the scheme is unsupported.
    """
    cfg = settings or app_settings
    candidate = (secret or "").strip()
    if not is_valid_secret_key(candidate):
        raise SecretKeyEncryptionError(
            SecretKeyEncryptionError.REASON_INVALID_SECRET,
            "Value is not a valid Stellar secret key (expected 'S' + 55 base32 chars).",
        )

    resolved = _resolve_scheme(scheme, cfg)
    key = _derive_key(resolved, cfg)
    plaintext = candidate.encode("ascii")

    if resolved == SCHEME_FERNET:
        payload = Fernet(base64.urlsafe_b64encode(key)).encrypt(plaintext).decode("ascii")
    else:
        nonce = os.urandom(_AESGCM_NONCE_BYTES)
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
        payload = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    return f"{TOKEN_PREFIX}.{resolved}.{payload}"


def _split_token(token: str) -> tuple[str, str]:
    """Split a stored ciphertext into ``(scheme, payload)``."""
    raw = (token or "").strip()
    if not raw:
        raise SecretKeyEncryptionError(
            SecretKeyEncryptionError.REASON_MALFORMED_TOKEN,
            "Ciphertext must not be empty.",
        )
    if raw.startswith(f"{TOKEN_PREFIX}."):
        scheme, _, payload = raw[len(TOKEN_PREFIX) + 1 :].partition(".")
        if scheme and payload:
            return scheme.lower(), payload
    raise SecretKeyEncryptionError(
        SecretKeyEncryptionError.REASON_MALFORMED_TOKEN,
        f"Ciphertext is not a recognised {TOKEN_PREFIX} token.",
    )


def decrypt_secret_key(token: str, settings: Optional[Settings] = None) -> str:
    """Decrypt a stored secret key token back to its plaintext seed.

    Prefer :func:`signing_key`, which scrubs the plaintext once the signing
    callback returns; use this directly only when the caller manages the
    lifetime of the plaintext itself.

    Raises:
        SecretKeyEncryptionError: on a malformed token, an unknown scheme,
            or authentication failure (wrong key or tampered ciphertext).
    """
    cfg = settings or app_settings
    scheme, payload = _split_token(token)
    if scheme not in SUPPORTED_SCHEMES:
        raise SecretKeyEncryptionError(
            SecretKeyEncryptionError.REASON_UNSUPPORTED_SCHEME,
            f"Ciphertext declares unsupported scheme {scheme!r}.",
        )

    key = _derive_key(scheme, cfg)
    try:
        if scheme == SCHEME_FERNET:
            plaintext = Fernet(base64.urlsafe_b64encode(key)).decrypt(
                payload.encode("ascii")
            )
        else:
            blob = base64.urlsafe_b64decode(payload.encode("ascii"))
            nonce, ciphertext = blob[:_AESGCM_NONCE_BYTES], blob[_AESGCM_NONCE_BYTES:]
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
    except (InvalidToken, InvalidTag) as exc:
        raise SecretKeyEncryptionError(
            SecretKeyEncryptionError.REASON_DECRYPTION_FAILED,
            "Ciphertext failed authentication — wrong key or tampered data.",
        ) from exc
    except Exception as exc:  # noqa: BLE001 - never leak key material
        raise SecretKeyEncryptionError(
            SecretKeyEncryptionError.REASON_DECRYPTION_FAILED,
            f"Could not decrypt secret key ({type(exc).__name__}).",
        ) from exc

    return plaintext.decode("ascii")


@contextmanager
def signing_key(
    token: str, settings: Optional[Settings] = None
) -> Iterator[str]:
    """Yield the decrypted secret key for the duration of the block only.

    The plaintext buffer is zeroed on exit — including when the block
    raises — so a decrypted operator key never outlives the signing
    operation that needed it.
    """
    plaintext = decrypt_secret_key(token, settings)
    buffer = bytearray(plaintext.encode("ascii"))
    try:
        yield plaintext
    finally:
        for i in range(len(buffer)):
            buffer[i] = 0
        del buffer
        del plaintext


def sign_with_secret_key(
    token: str,
    signer: Callable[[str], T],
    settings: Optional[Settings] = None,
) -> T:
    """Run *signer* with the decrypted secret key and return its result.

    The plaintext exists only for the duration of the call:

        signed = sign_with_secret_key(
            encrypted_key,
            lambda secret: transaction.sign(Keypair.from_secret(secret)),
        )
    """
    with signing_key(token, settings) as secret:
        return signer(secret)


def load_operator_secret_token(settings: Optional[Settings] = None) -> str:
    """Return the configured encrypted operator secret key token.

    Raises:
        SecretKeyEncryptionError: when ``STELLAR_OPERATOR_SECRET_ENCRYPTED``
            is unset — payouts must fail closed rather than fall back to a
            plaintext key from somewhere else.
    """
    cfg = settings or app_settings
    token = (cfg.STELLAR_OPERATOR_SECRET_ENCRYPTED or "").strip()
    if not token:
        raise SecretKeyEncryptionError(
            SecretKeyEncryptionError.REASON_NOT_CONFIGURED,
            "STELLAR_OPERATOR_SECRET_ENCRYPTED is not configured.",
        )
    return token


@contextmanager
def operator_signing_key(settings: Optional[Settings] = None) -> Iterator[str]:
    """Yield the decrypted operator secret key for one signing operation."""
    with signing_key(load_operator_secret_token(settings), settings) as secret:
        yield secret
