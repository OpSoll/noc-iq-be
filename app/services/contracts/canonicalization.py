"""#357 – Contract adapter request canonicalization and signature stability.

Ensures that logically-equivalent contract requests always produce the same
cryptographic hash regardless of:

* JSON key ordering
* Numeric representation (``1.0`` vs ``1.00`` vs ``1.000``)
* Presence of null / empty fields

Provides ``CanonicalRequestBuilder`` with ``build()``, ``hash()``, and
``verify()`` methods.
"""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from typing import Any

from app.core.config import settings

# Configurable salt for additional entropy in signatures.
CONTRACT_CANONICAL_SALT: str = getattr(settings, "CONTRACT_CANONICAL_SALT", "")


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _strip_trailing_zeros(value: str) -> str:
    """Remove trailing zeros after the decimal point, keeping at least one
    digit after '.' for integer-like decimals."""
    if "." not in value:
        return value
    value = value.rstrip("0").rstrip(".")
    # Re-add a trailing zero if the value ends with '.' (e.g. "100." → "100")
    return value


def _normalize_numeric(value: Any) -> Any:
    """Normalise a numeric value to a canonical string representation."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return value
        # Convert to Decimal to strip trailing zeros reliably
        d = Decimal(str(value))
        # Normalize to a canonical form (remove exponent notation)
        normalized = d.normalize()
        # Convert back to string and strip trailing zeros
        return _strip_trailing_zeros(str(normalized))
    if isinstance(value, str):
        # Attempt numeric parsing for string-encoded numbers
        try:
            d = Decimal(value)
            return _strip_trailing_zeros(str(d.normalize()))
        except Exception:
            return value
    return value


def _remove_nulls_and_empty(obj: Any) -> Any:
    """Recursively strip keys with ``None`` / empty-string / empty-dict / empty-list values."""
    if isinstance(obj, dict):
        return {
            k: _remove_nulls_and_empty(v)
            for k, v in obj.items()
            if v is not None and v != "" and v != {} and v != []
        }
    if isinstance(obj, list):
        return [_remove_nulls_and_empty(item) for item in obj]
    return obj


def _canonicalize(obj: Any) -> Any:
    """Deeply normalise a JSON-serialisable object for deterministic hashing."""
    if isinstance(obj, dict):
        return {
            k: _canonicalize(_normalize_numeric(v))
            for k, v in sorted(obj.items())
            if v is not None and v != "" and v != {} and v != []
        }
    if isinstance(obj, list):
        return [_canonicalize(_normalize_numeric(item)) for item in obj]
    return _normalize_numeric(obj)


# ---------------------------------------------------------------------------
# CanonicalRequestBuilder
# ---------------------------------------------------------------------------

class CanonicalRequestBuilder:
    """Build a deterministic canonical representation of a contract request
    and compute a SHA-256 signature over it.

    Usage::

        builder = CanonicalRequestBuilder()
        canonical = builder.build(params)
        h = builder.hash()
        assert builder.verify(params, h)
    """

    def __init__(self, salt: str | None = None) -> None:
        self._salt = salt or CONTRACT_CANONICAL_SALT
        self._canonical: dict[str, Any] | None = None

    # -- public API ----------------------------------------------------------

    def build(self, params: dict[str, Any]) -> dict[str, Any]:
        """Produce and store the canonical form of *params*."""
        cleaned = _remove_nulls_and_empty(params)
        self._canonical = _canonicalize(cleaned)
        return self._canonical

    def hash(self) -> str:
        """Return the hex SHA-256 digest of the canonical representation.

        Raises ``RuntimeError`` if ``build()`` has not been called yet.
        """
        if self._canonical is None:
            raise RuntimeError("Call build() before hash()")

        payload = json.dumps(
            self._canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        # Prepend salt for domain separation
        salted = self._salt + payload
        return hashlib.sha256(salted.encode("utf-8")).hexdigest()

    def verify(self, params: dict[str, Any], expected_hash: str) -> bool:
        """Return ``True`` if canonicalising *params* produces *expected_hash*."""
        self.build(params)
        return self.hash() == expected_hash

    # -- convenience ---------------------------------------------------------

    @property
    def canonical(self) -> dict[str, Any] | None:
        return self._canonical
