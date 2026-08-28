"""Middleware to enforce correlation ID on all response paths, including errors."""

from fastapi import Request
from fastapi.responses import JSONResponse
from app.utils.correlation import get_correlation_id, generate_correlation_id


async def correlation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Ensure error responses always include correlation_id."""
    corr_id = get_correlation_id() or generate_correlation_id()
    return JSONResponse(
        status_code=500,
        content={
            "error": str(exc),
            "correlation_id": corr_id,
        },
        headers={"X-Correlation-ID": corr_id},
    )
