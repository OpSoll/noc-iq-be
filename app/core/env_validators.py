import re
from urllib.parse import urlparse
from typing import Any


def validate_postgres_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in ("postgresql", "postgresql+asyncpg", "postgres"):
        raise ValueError(f"DATABASE_URL must be a valid PostgreSQL URL, got scheme '{parsed.scheme}'")
    if not parsed.hostname:
        raise ValueError("DATABASE_URL must include a hostname")
    return value


def validate_redis_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in ("redis", "rediss", "redis+sentinel"):
        raise ValueError(f"CELERY_BROKER_URL must be a valid Redis URL, got scheme '{parsed.scheme}'")
    if not parsed.hostname:
        raise ValueError("CELERY_BROKER_URL must include a hostname")
    return value


def validate_min_length(value: str, min_length: int, field_name: str) -> str:
    if len(value) < min_length:
        raise ValueError(f"{field_name} must be at least {min_length} characters long")
    return value


def validate_allowed_origins(value: str) -> list[str]:
    origins = [o.strip() for o in value.split(",") if o.strip()]
    for origin in origins:
        if not re.match(r"^https?://.+$", origin) and origin != "*":
            raise ValueError(
                f"ALLOWED_ORIGINS entry '{origin}' must be a valid URL starting with http:// or https://, or '*'"
            )
    return origins


def validate_positive_int(value: Any, field_name: str) -> int:
    try:
        int_val = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be a positive integer")
    if int_val <= 0:
        raise ValueError(f"{field_name} must be greater than 0")
    return int_val
