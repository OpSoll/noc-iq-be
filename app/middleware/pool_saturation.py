import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.db.session import pool_health
from app.core.config import settings


class PoolSaturationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self._saturated_since = None

    async def dispatch(self, request: Request, call_next):
        if pool_health.is_saturated():
            now = time.time()
            if self._saturated_since is None:
                self._saturated_since = now
            elif now - self._saturated_since > settings.DB_POOL_REJECT_AFTER_SECONDS:
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Service temporarily unavailable: connection pool exhausted"},
                )
            return JSONResponse(
                status_code=530,
                content={"detail": "Connection pool saturation detected"},
            )
        else:
            self._saturated_since = None

        return await call_next(request)
