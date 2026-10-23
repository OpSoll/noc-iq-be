"""Cross-cutting request and response guardrails.

Holds the request body size limit (#767) and the standard API response
envelope (#768).
"""

import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.utils.correlation import get_or_generate_correlation_id

#: Maximum accepted request body size (10 MB).
MAX_CONTENT_LENGTH = 10 * 1024 * 1024

#: Responses larger than this are passed through unwrapped, so the envelope
#: interceptor never buffers an unbounded body.
MAX_ENVELOPE_BUFFER_BYTES = 8 * 1024 * 1024


# ---------------------------------------------------------------------------
# Request payload size limiting (#767)
# ---------------------------------------------------------------------------


def parse_content_length(raw: Optional[str]) -> Optional[int]:
    """Parse a ``Content-Length`` header, tolerating malformed values.

    Returns ``None`` when the header is absent or not a non-negative integer,
    so a bogus header is treated as "unknown length" rather than raising.
    """
    if raw is None:
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


async def check_payload_size(request: Request):
    """Reject a request whose declared body size exceeds the limit."""
    declared = parse_content_length(request.headers.get("content-length"))
    if declared is not None and declared > MAX_CONTENT_LENGTH:
        raise HTTPException(status_code=413, detail="Payload too large")


def _scope_header(scope: dict, name: bytes) -> Optional[str]:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1")
    return None


class PayloadSizeLimitMiddleware:
    """Pure-ASGI guard enforcing the request body size limit.

    Oversized requests are rejected on the declared ``Content-Length`` before
    the body is read at all. For chunked requests that declare no length, the
    body is counted as it streams and the read is aborted once the running
    total passes the limit, so an oversized upload is never fully buffered.
    """

    def __init__(self, app, max_content_length: int = MAX_CONTENT_LENGTH):
        self.app = app
        self.max_content_length = max_content_length

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = parse_content_length(_scope_header(scope, b"content-length"))
        if declared is not None and declared > self.max_content_length:
            response = JSONResponse({"detail": "Payload too large"}, status_code=413)
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_content_length:
                    raise HTTPException(status_code=413, detail="Payload too large")
            return message

        await self.app(scope, limited_receive, send)


# ---------------------------------------------------------------------------
# Standard response envelope (#768)
# ---------------------------------------------------------------------------


def build_envelope(
    data: Any,
    *,
    page: Optional[int] = None,
    limit: Optional[int] = None,
    total: Optional[int] = None,
    correlation_id: Optional[str] = None,
) -> dict[str, Any]:
    """Wrap ``data`` in the standard ``{success, data, metadata}`` envelope.

    Pagination keys are only included when at least one of ``page``,
    ``limit`` or ``total`` is supplied, which is how collection endpoints
    opt in.
    """
    metadata: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id or get_or_generate_correlation_id(),
    }
    if page is not None or limit is not None or total is not None:
        metadata["pagination"] = {"page": page, "limit": limit, "total": total}
    return {"success": True, "data": data, "metadata": metadata}


def _is_json_response(headers: list) -> bool:
    for key, value in headers:
        if key.lower() == b"content-type" and b"application/json" in value.lower():
            return True
    return False


def _with_content_length(headers: list, length: int) -> list:
    updated = [(k, v) for k, v in headers if k.lower() != b"content-length"]
    updated.append((b"content-length", str(length).encode("latin-1")))
    return updated


class ResponseEnvelopeMiddleware:
    """Wraps successful JSON responses in the standard envelope.

    Only ``2xx`` JSON responses are wrapped, and a payload that is already an
    envelope is left alone so the interceptor is safe to apply globally.
    Error responses keep their existing shape, leaving the error contract in
    ``app/core/exceptions.py`` untouched.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        state: dict[str, Any] = {"status": None, "headers": []}
        chunks: list = []

        async def capture(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                state["headers"] = list(message.get("headers", []))
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
            # Everything is replayed once the body is complete.

        await self.app(scope, receive, capture)

        status = state["status"] or 500
        headers = state["headers"]
        body = b"".join(chunks)

        if 200 <= status < 300 and _is_json_response(headers) and len(body) <= MAX_ENVELOPE_BUFFER_BYTES:
            try:
                payload = json.loads(body or b"null")
            except (ValueError, UnicodeDecodeError):
                payload = None
            else:
                if not (isinstance(payload, dict) and "success" in payload):
                    body = json.dumps(build_envelope(payload), default=str).encode("utf-8")
                    headers = _with_content_length(headers, len(body))

        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})
