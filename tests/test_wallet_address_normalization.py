"""Wallet address normalisation tests (#902).

``WalletLinkRequest`` is the entry point that normalises and validates Stellar
public keys before the registry stores them.
"""

import pytest
from pydantic import ValidationError

from app.models.wallet import WalletLinkRequest

VALID_KEY = "G" + "A" * 55  # G + 55 base32 characters


def test_valid_address_is_accepted():
    request = WalletLinkRequest(user_id="user-1", public_key=VALID_KEY)

    assert request.public_key == VALID_KEY


def test_surrounding_whitespace_is_trimmed():
    request = WalletLinkRequest(user_id="user-1", public_key=f"  {VALID_KEY}  ")

    assert request.public_key == VALID_KEY


def test_lowercase_address_is_rejected():
    with pytest.raises(ValidationError):
        WalletLinkRequest(user_id="user-1", public_key=VALID_KEY.lower())


def test_too_short_address_is_rejected():
    with pytest.raises(ValidationError):
        WalletLinkRequest(user_id="user-1", public_key=VALID_KEY[:-1])


def test_invalid_base32_characters_are_rejected():
    with pytest.raises(ValidationError):
        WalletLinkRequest(user_id="user-1", public_key="G" + "1" * 55)


def test_wrong_prefix_is_rejected():
    with pytest.raises(ValidationError):
        WalletLinkRequest(user_id="user-1", public_key="S" + "A" * 55)
