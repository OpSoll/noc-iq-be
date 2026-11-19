from datetime import datetime, timedelta, timezone

from app.core.api_keys import is_api_key_expired

EXPIRY = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_api_key_expiry_boundary():
    assert not is_api_key_expired(EXPIRY, EXPIRY - timedelta(seconds=1))
    assert is_api_key_expired(EXPIRY, EXPIRY)
    assert is_api_key_expired(EXPIRY, EXPIRY + timedelta(seconds=1))


def test_api_key_without_expiry_never_expires():
    assert not is_api_key_expired(None, EXPIRY)


def test_api_key_naive_and_aware_are_consistent():
    naive = EXPIRY.replace(tzinfo=None)
    assert is_api_key_expired(naive, EXPIRY)
    assert not is_api_key_expired(EXPIRY, naive - timedelta(seconds=1))
