"""HTTP ETag helpers for idempotent GET endpoints (Issue #503).

Conditional GET support lets clients revalidate cached representations
without re-downloading identical payloads:

- The response payload is hashed (MD5) into a quoted ``ETag`` header.
- When the client sends that tag back in ``If-None-Match``, the endpoint
  returns ``304 Not Modified`` with an empty body, cutting bandwidth.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import Request, Response


def compute_etag(payload: Any) -> str:
    """Return a quoted MD5 ETag for a JSON-serializable payload.

    The payload is serialized with a stable key order so identical bodies
    always produce identical tags regardless of dict insertion order.
    """
    body = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f'"{hashlib.md5(body).hexdigest()}"'


def apply_etag(response: Response, request: Request, payload: Any) -> Any:
    """Set ``ETag`` on the response and honor ``If-None-Match``.

    Returns the payload to return from the endpoint, or ``None`` when the
    client's ``If-None-Match`` header matches (callers should then return a
    ``304 Not Modified`` response instead of the body).
    """
    etag = compute_etag(payload)
    response.headers["ETag"] = etag
    # Always revalidate so the tag stays authoritative without a stale TTL.
    response.headers["Cache-Control"] = "no-cache"
    if request.headers.get("If-None-Match") == etag:
        response.status_code = 304
        return None
    return payload
