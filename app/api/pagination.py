"""Shared query constraints for offset and page based API pagination."""

from fastapi import Query


def page_number(default: int = 1, description: str | None = None):
    """Return the standard one-indexed page query parameter."""
    return Query(default=default, ge=1, description=description)


def page_size(default: int = 20, maximum: int = 100, description: str | None = None):
    """Return the standard positive, capped page-size query parameter."""
    return Query(default=default, ge=1, le=maximum, description=description)


def limit(default: int = 50, maximum: int = 200, description: str | None = None):
    """Return a positive, capped limit query parameter."""
    return Query(default=default, ge=1, le=maximum, description=description)


def offset(default: int = 0, description: str | None = None):
    """Return a nonnegative offset query parameter."""
    return Query(default=default, ge=0, description=description)
