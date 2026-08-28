import time
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from app.utils.logging import get_structured_logger

logger = get_structured_logger("latency_middleware")


class LatencyLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to measure, log, and warn about API request processing latency."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.perf_counter()
        
        response = await call_next(request)
        
        process_time_ms = (time.perf_counter() - start_time) * 1000
        
        log_kwargs = {
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "process_time_ms": round(process_time_ms, 2)
        }
        
        if process_time_ms > 500:
            logger.warning(
                f"Slow request detected: {request.method} {request.url.path} took {process_time_ms:.2f}ms",
                **log_kwargs
            )
        else:
            logger.info(
                f"Request completed: {request.method} {request.url.path} took {process_time_ms:.2f}ms",
                **log_kwargs
            )
            
        return response
