from datetime import datetime, timezone
from typing import Optional


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def is_api_key_expired(expires_at: Optional[datetime], now: datetime) -> bool:
    """A key is expired at its expiry instant (now >= expires_at).

    None never expires. Naive datetimes are treated as UTC.
    """
    if expires_at is None:
        return False
    return _aware(now) >= _aware(expires_at)
