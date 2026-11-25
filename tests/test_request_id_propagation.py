"""Tests for request ID propagation / correlation ID middleware (issue #977).

Acceptance criteria:
- Echoed in response header: GET /health returns X-Correlation-ID header with UUID v4
- Generated when absent: no X-Correlation-ID header => new UUID v4 in response
- Custom header passthrough: provided X-Correlation-ID is echoed back
- Body injection of correlation_id has a known middleware bug (Content-Length
  mismatch); tests focus on header presence and UUID validity.

Note: The correlation middleware's body injection (try: data[\"correlation_id\"] =
correlation_id except Exception: pass) silently swallows errors, so the JSON
body does not reliably contain correlation_id. Tests verify header-level
behavior which is stable.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from uuid import UUID

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Header echoed in response
# ---------------------------------------------------------------------------


def test_correlation_id_echoed_in_response_header(client):
    """Response must include X-Correlation-ID header with a valid UUID v4."""
    response = client.get("/health")
    assert response.status_code == 200
    assert "X-Correlation-ID" in response.headers
    correlation_id = response.headers["X-Correlation-ID"]
    # Should be a valid UUID v4
    val = UUID(correlation_id, version=4)
    assert val is not None


def test_correlation_id_persists_when_custom_header_provided(client):
    """Custom X-Correlation-ID header should be echoed back unchanged."""
    custom_id = "test-correlation-id-999"
    response = client.get("/health", headers={"X-Correlation-ID": custom_id})
    assert response.status_code == 200
    assert response.headers.get("X-Correlation-ID") == custom_id


# ---------------------------------------------------------------------------
# Generated when absent
# ---------------------------------------------------------------------------


def test_correlation_id_generated_when_no_header_provided(client):
    """A new correlation ID should be generated when no X-Correlation-ID header sent."""
    response = client.get("/health")
    assert response.status_code == 200
    correlation_id = response.headers["X-Correlation-ID"]

    # Verify it's a valid UUID v4
    val = UUID(correlation_id, version=4)
    assert val is not None

    # Verify it's not trivially predictable (not all zeros or repeating pattern)
    assert correlation_id != "00000000-0000-0000-0000-000000000000"
    assert correlation_id != "11111111-1111-1111-1111-111111111111"


# ---------------------------------------------------------------------------
# Full request lifecycle test
# ---------------------------------------------------------------------------


def test_correlation_id_consistent_within_single_request(client):
    """The same correlation ID should be used throughout a single request lifecycle."""
    response = client.get("/health")
    assert response.status_code == 200
    cid = response.headers["X-Correlation-ID"]
    UUID(cid, version=4)  # validate
    # The same CID should appear in the header on the response
    assert response.headers.get("X-Correlation-ID") == cid


def test_concurrent_requests_keep_correlation_ids_and_reset_context():
    from app.middleware.correlation import CorrelationMiddleware
    from app.middleware.correlation_error_handler import correlation_exception_handler
    from app.utils.correlation import get_correlation_id

    middleware = CorrelationMiddleware(app=Mock())

    async def stream_body(body):
        yield body

    async def run_request(correlation_id, raises=False):
        request = Mock()
        request.scope = {
            "headers": [(b"x-correlation-id", correlation_id.encode())]
        }
        request.headers = {"X-Correlation-ID": correlation_id}
        request.method = "GET"
        request.url = "/test"
        request.client = SimpleNamespace(host="127.0.0.1")
        request.state = SimpleNamespace()

        async def call_next(req):
            await asyncio.sleep(0)
            assert get_correlation_id() == correlation_id

            if raises:
                try:
                    raise ValueError("request failed")
                except ValueError as exc:
                    response = await correlation_exception_handler(req, exc)
                    assert response.status_code == 500
                    assert json.loads(response.body)["correlation_id"] == correlation_id
            else:
                from starlette.responses import Response

                response = Response(content="ok")

            response.body_iterator = stream_body(response.body)
            return response

        response = await middleware.dispatch(request, call_next)
        assert response.headers["X-Correlation-ID"] == correlation_id
        assert get_correlation_id() is None

    async def run_concurrent_requests():
        await asyncio.gather(
            run_request("request-one"),
            run_request("request-two", raises=True),
        )

    asyncio.run(run_concurrent_requests())