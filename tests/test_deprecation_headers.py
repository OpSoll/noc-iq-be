"""Tests for the V0 API deprecation header middleware (Issue #511).

Legacy ``/api/v0/*`` routes must respond with RFC 8594 ``Deprecation`` and
``Sunset`` headers so clients can plan their migration away from V0.
"""
from fastapi.testclient import TestClient

V0_DEPRECATION_HEADER = "@1735689600"
V0_SUNSET_HEADER = "Sat, 31 Dec 2026 23:59:59 GMT"


def test_v0_route_gets_deprecation_headers(client: TestClient):
    # No V0 routes are registered yet, so a 404 is expected — the middleware
    # must still stamp the deprecation headers on every V0 response.
    response = client.get("/api/v0/legacy/outages")
    assert response.status_code == 404
    assert response.headers.get("Deprecation") == V0_DEPRECATION_HEADER
    assert response.headers.get("Sunset") == V0_SUNSET_HEADER


def test_v0_route_includes_headers_on_all_methods(client: TestClient):
    response = client.post("/api/v0/legacy/disputes", json={})
    assert response.status_code == 404
    assert response.headers.get("Deprecation") == V0_DEPRECATION_HEADER
    assert response.headers.get("Sunset") == V0_SUNSET_HEADER


def test_v1_routes_do_not_get_v0_deprecation_headers(client: TestClient):
    response = client.get("/api/v1/outages/")
    assert response.status_code in {401, 403}
    # V0 headers must not leak onto V1 responses (V1 has its own Deprecation
    # marker from a separate middleware, but never the V0 epoch value).
    assert response.headers.get("Deprecation") != V0_DEPRECATION_HEADER
    assert response.headers.get("Sunset") != V0_SUNSET_HEADER
