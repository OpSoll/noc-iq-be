import time
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.utils.correlation import get_or_generate_correlation_id, set_correlation_id
from app.utils.logging import get_structured_logger

logger = get_structured_logger("correlation_middleware")


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Middleware to add correlation IDs to requests and enable request tracing."""
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Extract correlation ID from header or generate new one
        correlation_id = request.headers.get("X-Correlation-ID") or get_or_generate_correlation_id()
        set_correlation_id(correlation_id)
        
        # Add correlation ID to request state for easy access
        request.state.correlation_id = correlation_id
        
        # Record request start time
        start_time = time.time()
        
        # Log incoming request
        logger.info(
            "Incoming request",
            method=request.method,
            url=str(request.url),
            client_host=request.client.host if request.client else None,
            user_agent=request.headers.get("User-Agent"),
            correlation_id=correlation_id
        )
        
        try:
            # Process the request
            response = await call_next(request)
            
            # Calculate request duration
            duration_ms = (time.time() - start_time) * 1000
            
            # Add correlation ID to response headers (mandatory for all paths)
            response.headers["X-Correlation-ID"] = correlation_id
            # Also inject into JSON body for error consumers
            try:
                body = b""
                async for chunk in response.body_iterator:
                    body += chunk
                response.body_iterator = _single_body_iterator(body)
                import json
                data = json.loads(body) if body else {}
                if isinstance(data, dict):
                    data["correlation_id"] = correlation_id
                    response.headers["Content-Length"] = str(len(json.dumps(data)))
            except Exception:
                pass
            
            # Log outgoing response
            logger.info(
                "Request completed",
                method=request.method,
                url=str(request.url),
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
                correlation_id=correlation_id
            )
            
            return response
            
        except Exception as exc:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(
                "Request failed",
                method=request.method,
                url=str(request.url),
                error=str(exc),
                duration_ms=round(duration_ms, 2),
                correlation_id=correlation_id
            )
            raise
