"""Helpers for keeping domain enum serialization consistent."""

from enum import Enum
from typing import Any


def enum_value(value: Any) -> Any:
    """Return an enum's wire value while leaving non-enum values unchanged."""
    return value.value if isinstance(value, Enum) else value
