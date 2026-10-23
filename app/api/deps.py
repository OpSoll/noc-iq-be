"""Shared FastAPI dependencies: database session, RBAC, callback signatures."""

from __future__ import annotations

import hashlib
import hmac
import time
from enum import Enum
from typing import Any, Callable, Iterable

from fastapi import HTTPException, Request, Security
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN

from app.core.auth import JWTBearer
from app.core.config import settings
from app.db.session import get_db

__all__ = [
    "get_db",
    "UserRole",
    "InsufficientPermissions",
    "require_role",
    "InvalidSignature",
    "verify_callback_signature",
    "compute_callback_signature",
]


# ---------------------------------------------------------------------------
# Role-based access control (#761)
# ---------------------------------------------------------------------------


class UserRole(str, Enum):
    viewer = "viewer"
    operator = "operator"
    admin = "admin"


#: Higher rank implies every capability of the ranks below it.
_ROLE_RANK: dict[UserRole, int] = {
    UserRole.viewer: 1,
    UserRole.operator: 2,
    UserRole.admin: 3,
}

bearer_scheme = JWTBearer()


class InsufficientPermissions(HTTPException):
    """403 raised when an authenticated user does not hold a required role."""

    def __init__(self, required: Iterable[str], actual: str) -> None:
        required_str = ", ".join(sorted(required)) or "any known role"
        super().__init__(
            status_code=HTTP_403_FORBIDDEN,
            detail=(
                f"InsufficientPermissions: role '{actual}' may not access this "
                f"endpoint. Required one of: {required_str}."
            ),
        )
        self.error_code = "insufficient_permissions"


def _coerce_role(value: "UserRole | str") -> UserRole:
    try:
        return UserRole(str(value).lower())
    except ValueError:
        known = ", ".join(r.value for r in UserRole)
        raise ValueError(f"Unknown role {value!r}. Expected one of: {known}.")


def require_role(
    *allowed: "UserRole | str",
    minimum: "UserRole | str | None" = None,
) -> Callable[..., Any]:
    """Build a dependency that enforces role-based access on an endpoint.

    Endpoints opt in by declaring the returned dependency. Pass the roles that
    are allowed outright, a ``minimum`` rank that any role at or above it
    satisfies, or both::

        # Exactly admin.
        @router.delete("/outages/{id}", dependencies=[Depends(require_role("admin"))])

        # Admin or operator — a role combination.
        @router.post("/outages", dependencies=[Depends(require_role("admin", "operator"))])

        # Anything at or above viewer (i.e. any authenticated role).
        @router.get("/outages", dependencies=[Depends(require_role(minimum="viewer"))])

    Roles are read from the ``role`` claim of the bearer token, defaulting to
    ``viewer`` when the claim is absent.
    """

    allowed_roles = {_coerce_role(role) for role in allowed}
    floor = _ROLE_RANK[_coerce_role(minimum)] if minimum is not None else None

    async def dependency(
        payload: dict[str, Any] = Security(bearer_scheme),
    ) -> dict[str, Any]:
        actual = str(payload.get("role") or UserRole.viewer.value)
        try:
            actual_role = _coerce_role(actual)
        except ValueError:
            raise InsufficientPermissions(
                (r.value for r in allowed_roles) or [r.value for r in UserRole], actual
            )

        permitted = actual_role in allowed_roles if allowed_roles else True
        if floor is not None and _ROLE_RANK[actual_role] < floor:
            permitted = False

        if not permitted:
            raise InsufficientPermissions(
                (r.value for r in allowed_roles) or [r.value for r in UserRole],
                actual,
            )
        return payload

    return dependency


# ---------------------------------------------------------------------------
# Webhook callback signature verification (#762)
# ---------------------------------------------------------------------------

SIGNATURE_HEADER = "X-Signature"
TIMESTAMP_HEADER = "X-Timestamp"

#: How far a callback timestamp may drift from now before it is treated as a replay.
DEFAULT_REPLAY_TOLERANCE_SECONDS = 300


class InvalidSignature(HTTPException):
    """401 raised when an inbound callback cannot be trusted."""

    def __init__(self, reason: str) -> None:
        super().__init__(
            status_code=HTTP_401_UNAUTHORIZED,
            detail=f"InvalidSignature: {reason}",
        )
        self.error_code = "invalid_signature"


def _partner_secret(secret: str | None) -> str:
    return secret or getattr(settings, "PAYMENT_WEBHOOK_SECRET", "") or settings.SECRET_KEY


def compute_callback_signature(payload: bytes, secret: str, timestamp: str) -> str:
    """Return the hex HMAC-SHA256 of ``{timestamp}.{payload}``."""
    message = timestamp.encode("utf-8") + b"." + payload
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


async def verify_callback_signature(
    request: Request,
    secret: str | None = None,
    tolerance_seconds: int = DEFAULT_REPLAY_TOLERANCE_SECONDS,
) -> str:
    """Dependency that verifies the signature on an inbound callback.

    Rejects the request with 401 when the ``X-Signature`` or ``X-Timestamp``
    headers are missing, when the timestamp is not an integer, when it falls
    outside the replay tolerance, or when the signature does not match the
    HMAC of the raw body. Returns the accepted signature on success.
    """
    signature = request.headers.get(SIGNATURE_HEADER)
    timestamp = request.headers.get(TIMESTAMP_HEADER)

    if not signature or not timestamp:
        raise InvalidSignature(f"missing {SIGNATURE_HEADER} or {TIMESTAMP_HEADER} header.")

    try:
        sent_at = int(timestamp)
    except (TypeError, ValueError):
        raise InvalidSignature("timestamp is not an integer.")

    if abs(int(time.time()) - sent_at) > tolerance_seconds:
        raise InvalidSignature("timestamp is outside the replay tolerance.")

    provided = signature.strip()
    if provided.lower().startswith("sha256="):
        provided = provided.split("=", 1)[1]

    body = await request.body()
    expected = compute_callback_signature(body, _partner_secret(secret), timestamp)
    if not hmac.compare_digest(expected, provided):
        raise InvalidSignature("signature does not match the request body.")

    return provided
