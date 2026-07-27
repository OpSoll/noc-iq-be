from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse
from fastapi import status


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """
    Applies different request body size limits based on the route path prefix.
    Limits:
      - /api/v1/auth       -> 1 KB
      - /api/v1/outages    -> 10 KB (CRUD)
      - /api/v1/payments   -> 10 KB (CRUD)
      - /api/v1/wallets    -> 10 KB (CRUD)
      - /api/v1/sla        -> 10 KB (CRUD)
      - /bulk               -> 10 MB
      - /webhook            -> 1 MB
    """

    AUTH_LIMIT = 1 * 1024  # 1 KB
    CRUD_LIMIT = 10 * 1024  # 10 KB
    BULK_LIMIT = 10 * 1024 * 1024  # 10 MB
    WEBHOOK_LIMIT = 1 * 1024 * 1024  # 1 MB

    _ROUTE_LIMITS: list[tuple[str, int]] = [
        ("/bulk", BULK_LIMIT),
        ("/webhook", WEBHOOK_LIMIT),
        ("/api/v1/auth", AUTH_LIMIT),
        ("/api/v1/outages", CRUD_LIMIT),
        ("/api/v1/payments", CRUD_LIMIT),
        ("/api/v1/wallets", CRUD_LIMIT),
        ("/api/v1/sla", CRUD_LIMIT),
    ]

    _DEFAULT_LIMIT = CRUD_LIMIT

    _SIZE_LABELS = {1024: "1 KB", 10_240: "10 KB", 1_048_576: "1 MB", 10_485_760: "10 MB"}

    def _get_limit(self, path: str) -> tuple[int, str]:
        for prefix, limit in self._ROUTE_LIMITS:
            if path.startswith(prefix):
                return limit, self._SIZE_LABELS.get(limit, f"{limit} bytes")
        return self._DEFAULT_LIMIT, self._SIZE_LABELS.get(self._DEFAULT_LIMIT, f"{self._DEFAULT_LIMIT} bytes")

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        if request.method in ("GET", "DELETE", "OPTIONS", "HEAD"):
            return await call_next(request)

        content_length = request.headers.get("content-length")
        if content_length is None:
            return await call_next(request)

        limit, label = self._get_limit(request.url.path)
        body_size = int(content_length)

        if body_size > limit:
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={
                    "detail": f"Request body too large. Maximum allowed size for this endpoint is {label}.",
                    "max_size_bytes": limit,
                    "received_size_bytes": body_size,
                    "path": request.url.path,
                },
            )

        return await call_next(request)
