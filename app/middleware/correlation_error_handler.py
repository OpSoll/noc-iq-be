"""Middleware to enforce correlation ID on all response paths, including errors."""

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from app.utils.correlation import get_correlation_id, generate_correlation_id, set_correlation_id

CORRELATION_HEADER = "X-Correlation-ID"


def _request_correlation_id(request: Request) -> str:
    return (
        getattr(request.state, "correlation_id", None)
        or get_correlation_id()
        or generate_correlation_id()
    )


def ensure_correlation_header(request: Request, response: Response) -> str:
    """Attach the request correlation ID without duplicating an existing one."""
    corr_id = _request_correlation_id(request)
    set_correlation_id(corr_id)
    if CORRELATION_HEADER not in response.headers:
        response.headers[CORRELATION_HEADER] = corr_id
    return corr_id


async def correlation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Ensure mapped and unhandled error responses carry the correlation ID."""
    corr_id = _request_correlation_id(request)
    set_correlation_id(corr_id)

    status_code = exc.status_code if isinstance(exc, HTTPException) else 500
    error = exc.detail if isinstance(exc, HTTPException) else str(exc)
    headers = {
        key: value
        for key, value in (getattr(exc, "headers", None) or {}).items()
        if key.lower() != CORRELATION_HEADER.lower()
    }
    headers[CORRELATION_HEADER] = corr_id

    return JSONResponse(
        status_code=status_code,
        content={
            "error": error,
            "correlation_id": corr_id,
        },
        headers=headers,
    )
