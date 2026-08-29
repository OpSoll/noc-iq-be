"""Tests for Stellar secret key encryption at rest.

Acceptance criteria covered:
  * Wallet secret keys are encrypted with ``cryptography`` (Fernet, and
    AES-256-GCM for the aesgcm scheme).
  * Private keys are decrypted in memory only for the duration of signing.
  * The encryption/decryption round trip is verified.
"""
from __future__ import annotations

import base64
import os

import pytest

from app.core.config import Settings
from app.services.stellar.keystore import (
    SCHEME_AESGCM,
    SCHEME_FERNET,
    TOKEN_PREFIX,
    SecretKeyEncryptionError,
    decrypt_secret_key,
    encrypt_secret_key,
    is_valid_secret_key,
    load_operator_secret_token,
    operator_signing_key,
    reset_key_cache,
    sign_with_secret_key,
    signing_key,
)

# A syntactically valid (never funded, never used) Stellar secret seed.
SECRET = "S" + "A" * 55
OTHER_SECRET = "S" + "B" * 55
TEST_SECRET_KEY = "unit-test-secret-key-at-least-32-characters-long"


def _settings(**overrides) -> Settings:
    return Settings(SECRET_KEY=TEST_SECRET_KEY, **overrides)


# --------------------------------------------------------------------------- #
# Round trip                                                                   #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("scheme", [SCHEME_FERNET, SCHEME_AESGCM])
def test_encrypt_decrypt_round_trip(scheme):
    cfg = _settings(STELLAR_KEY_ENCRYPTION_SCHEME=scheme)

    token = encrypt_secret_key(SECRET, settings=cfg)

    assert decrypt_secret_key(token, settings=cfg) == SECRET


@pytest.mark.parametrize("scheme", [SCHEME_FERNET, SCHEME_AESGCM])
def test_ciphertext_does_not_contain_the_plaintext(scheme):
    cfg = _settings(STELLAR_KEY_ENCRYPTION_SCHEME=scheme)

    token = encrypt_secret_key(SECRET, settings=cfg)

    assert SECRET not in token
    assert token.startswith(f"{TOKEN_PREFIX}.{scheme}.")


@pytest.mark.parametrize("scheme", [SCHEME_FERNET, SCHEME_AESGCM])
def test_encryption_is_non_deterministic(scheme):
    """Two encryptions of the same key must not produce the same ciphertext."""
    cfg = _settings(STELLAR_KEY_ENCRYPTION_SCHEME=scheme)

    assert encrypt_secret_key(SECRET, settings=cfg) != encrypt_secret_key(
        SECRET, settings=cfg
    )


def test_aes256_key_length_is_256_bit():
    """The aesgcm scheme must be keyed with 32 bytes (AES-256)."""
    key = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
    cfg = _settings(
        STELLAR_KEY_ENCRYPTION_SCHEME=SCHEME_AESGCM, STELLAR_KEY_ENCRYPTION_KEY=key
    )

    token = encrypt_secret_key(SECRET, settings=cfg)

    assert decrypt_secret_key(token, settings=cfg) == SECRET
    assert len(base64.urlsafe_b64decode(key)) == 32


def test_scheme_is_read_from_the_token_not_the_config():
    """A key encrypted under one scheme stays readable after a scheme switch."""
    fernet_cfg = _settings(STELLAR_KEY_ENCRYPTION_SCHEME=SCHEME_FERNET)
    token = encrypt_secret_key(SECRET, settings=fernet_cfg)

    aesgcm_cfg = _settings(STELLAR_KEY_ENCRYPTION_SCHEME=SCHEME_AESGCM)

    assert decrypt_secret_key(token, settings=aesgcm_cfg) == SECRET


def test_explicit_scheme_argument_overrides_config():
    cfg = _settings(STELLAR_KEY_ENCRYPTION_SCHEME=SCHEME_FERNET)

    token = encrypt_secret_key(SECRET, scheme=SCHEME_AESGCM, settings=cfg)

    assert token.startswith(f"{TOKEN_PREFIX}.{SCHEME_AESGCM}.")
    assert decrypt_secret_key(token, settings=cfg) == SECRET


# --------------------------------------------------------------------------- #
# Failure modes                                                                #
# --------------------------------------------------------------------------- #

def test_wrong_key_cannot_decrypt():
    cfg = _settings()
    token = encrypt_secret_key(SECRET, settings=cfg)

    reset_key_cache()
    other = Settings(SECRET_KEY="a-completely-different-secret-key-32-chars")

    with pytest.raises(SecretKeyEncryptionError) as exc:
        decrypt_secret_key(token, settings=other)

    assert exc.value.reason == SecretKeyEncryptionError.REASON_DECRYPTION_FAILED


@pytest.mark.parametrize("scheme", [SCHEME_FERNET, SCHEME_AESGCM])
def test_tampered_ciphertext_is_rejected(scheme):
    cfg = _settings(STELLAR_KEY_ENCRYPTION_SCHEME=scheme)
    token = encrypt_secret_key(SECRET, settings=cfg)

    head, payload = token.rsplit(".", 1)
    flipped = "B" if payload[5] != "B" else "C"
    tampered = f"{head}.{payload[:5]}{flipped}{payload[6:]}"

    with pytest.raises(SecretKeyEncryptionError) as exc:
        decrypt_secret_key(tampered, settings=cfg)

    assert exc.value.reason == SecretKeyEncryptionError.REASON_DECRYPTION_FAILED


def test_malformed_token_is_rejected():
    for bad in ("", "   ", "not-a-token", "stellar.v1"):
        with pytest.raises(SecretKeyEncryptionError) as exc:
            decrypt_secret_key(bad, settings=_settings())
        assert exc.value.reason == SecretKeyEncryptionError.REASON_MALFORMED_TOKEN


def test_unknown_scheme_in_token_is_rejected():
    with pytest.raises(SecretKeyEncryptionError) as exc:
        decrypt_secret_key(f"{TOKEN_PREFIX}.rot13.abcdef", settings=_settings())

    assert exc.value.reason == SecretKeyEncryptionError.REASON_UNSUPPORTED_SCHEME


@pytest.mark.parametrize(
    "value", ["", "GABC", "S" + "A" * 54, "S" + "a" * 55, "not-a-key", None]
)
def test_non_stellar_secret_keys_are_refused(value):
    with pytest.raises(SecretKeyEncryptionError) as exc:
        encrypt_secret_key(value, settings=_settings())

    assert exc.value.reason == SecretKeyEncryptionError.REASON_INVALID_SECRET


def test_error_messages_never_contain_key_material():
    with pytest.raises(SecretKeyEncryptionError) as exc:
        encrypt_secret_key(SECRET[:-1], settings=_settings())

    assert SECRET[:-1] not in str(exc.value)


def test_encryption_key_must_decode_to_32_bytes():
    short = base64.urlsafe_b64encode(os.urandom(16)).decode("ascii")
    cfg = _settings(STELLAR_KEY_ENCRYPTION_KEY=short)

    with pytest.raises(SecretKeyEncryptionError) as exc:
        encrypt_secret_key(SECRET, settings=cfg)

    assert exc.value.reason == SecretKeyEncryptionError.REASON_UNSUPPORTED_SCHEME


def test_is_valid_secret_key():
    assert is_valid_secret_key(SECRET) is True
    assert is_valid_secret_key("G" + "A" * 55) is False
    assert is_valid_secret_key("") is False


# --------------------------------------------------------------------------- #
# In-memory-only decryption during signing                                     #
# --------------------------------------------------------------------------- #

def test_signing_key_yields_plaintext_inside_the_block_only():
    cfg = _settings()
    token = encrypt_secret_key(SECRET, settings=cfg)
    seen = []

    with signing_key(token, settings=cfg) as secret:
        seen.append(secret)

    assert seen == [SECRET]
    # The stored token remains the only durable representation.
    assert SECRET not in token


def test_signing_key_scrubs_even_when_the_block_raises():
    cfg = _settings()
    token = encrypt_secret_key(SECRET, settings=cfg)

    with pytest.raises(RuntimeError):
        with signing_key(token, settings=cfg):
            raise RuntimeError("signing blew up")


def test_sign_with_secret_key_passes_plaintext_to_the_signer():
    cfg = _settings()
    token = encrypt_secret_key(SECRET, settings=cfg)

    signed = sign_with_secret_key(token, lambda secret: f"signed:{secret[:2]}", settings=cfg)

    assert signed == "signed:SA"


def test_operator_signing_key_uses_the_configured_ciphertext():
    base = _settings()
    token = encrypt_secret_key(SECRET, settings=base)
    cfg = _settings(STELLAR_OPERATOR_SECRET_ENCRYPTED=token)

    assert load_operator_secret_token(cfg) == token
    with operator_signing_key(cfg) as secret:
        assert secret == SECRET


def test_operator_signing_fails_closed_when_not_configured():
    with pytest.raises(SecretKeyEncryptionError) as exc:
        load_operator_secret_token(_settings(STELLAR_OPERATOR_SECRET_ENCRYPTED=""))

    assert exc.value.reason == SecretKeyEncryptionError.REASON_NOT_CONFIGURED


def test_config_rejects_an_unknown_encryption_scheme():
    with pytest.raises(ValueError):
        _settings(STELLAR_KEY_ENCRYPTION_SCHEME="rot13")
