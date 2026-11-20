"""Unit tests for environment config validators (#896)."""

import pytest

from app.core.env_validators import (
    validate_allowed_origins,
    validate_min_length,
    validate_postgres_url,
    validate_redis_url,
)


def test_valid_postgres_url_is_returned_unchanged():
    url = "postgresql://user:pass@localhost:5432/nociq"

    assert validate_postgres_url(url) == url


def test_non_postgres_url_is_rejected():
    with pytest.raises(ValueError, match="DATABASE_URL"):
        validate_postgres_url("mysql://user:pass@localhost:3306/nociq")


def test_postgres_url_without_hostname_is_rejected():
    with pytest.raises(ValueError, match="hostname"):
        validate_postgres_url("postgresql:///nociq")


def test_valid_redis_url_is_returned_unchanged():
    url = "redis://localhost:6379/0"

    assert validate_redis_url(url) == url


def test_short_secret_is_rejected():
    with pytest.raises(ValueError, match="at least 32"):
        validate_min_length("too-short", 32, "SECRET_KEY")


def test_long_enough_secret_is_accepted():
    secret = "x" * 32

    assert validate_min_length(secret, 32, "SECRET_KEY") == secret


def test_allowed_origins_are_split_and_stripped():
    assert validate_allowed_origins("http://localhost:3000, https://example.com") == [
        "http://localhost:3000",
        "https://example.com",
    ]


def test_bad_origin_is_rejected():
    with pytest.raises(ValueError, match="ALLOWED_ORIGINS"):
        validate_allowed_origins("localhost:3000")


def test_wildcard_origin_is_allowed():
    assert validate_allowed_origins("*") == ["*"]
