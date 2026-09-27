import logging
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request

logger = logging.getLogger(__name__)

# RFC 8594 deprecation contract for legacy V0 endpoints (Issue #511).
_DEPRECATION_HEADER = "@1735689600"  # 2025-01-01T00:00:00Z epoch timestamp
_SUNSET_HEADER = "Sat, 31 Dec 2026 23:59:59 GMT"
_V0_PREFIX = "/api/v0"


class DeprecationHeaderMiddleware(BaseHTTPMiddleware):
    """Inject RFC 8594 ``Deprecation`` / ``Sunset`` headers on legacy V0 routes.

    Legacy ``/api/v0/*`` endpoints are being sunset; every response served
    under that prefix carries the standard deprecation headers so clients can
    plan their migration. Each legacy route invocation is also logged so the
    remaining V0 traffic can be measured.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Callable:
        is_legacy = request.url.path.startswith(_V0_PREFIX)
        if is_legacy:
            logger.info(
                "Legacy API V0 invocation",
                method=request.method,
                path=request.url.path,
            )
        response = await call_next(request)
        if is_legacy:
            response.headers["Deprecation"] = _DEPRECATION_HEADER
            response.headers["Sunset"] = _SUNSET_HEADER
        return response
