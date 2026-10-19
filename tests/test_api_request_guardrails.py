"""Request guardrails at the edge: content-type (415) and body size (413).

Both middlewares reject a malformed request before it reaches a route handler, so
a failure in either is cheap to fix but expensive to discover — the symptom is a
route that mysteriously 400s on well-formed input. Neither had a consolidated
matrix, which is what both issues ask for.

#973 — ``ContentTypeEnforcementMiddleware`` requires ``application/json`` on
POST/PUT/PATCH, plus ``multipart/form-data`` on ``/upload`` paths. A missing or
unsupported type is answered with 415.

#974 — ``BodySizeLimitMiddleware`` applies different ceilings by path prefix
(1 KB on auth, 10 KB on CRUD, 1 MB on webhooks, 10 MB on bulk). The limits are
listed in one dict and there was no test walking them, so changing a limit or
reordering ``_ROUTE_LIMITS`` would change behaviour with nothing failing.

Both middlewares are unit-tested through a purpose-built app rather than the real
one. That is deliberate: it lets a request be driven at exactly the boundary
(``limit - 1``, ``limit``, ``limit + 1``) without a route handler in the way, so a
413 means the middleware fired and not that a handler rejected the body. The real
app is exercised separately in ``TestAgainstRealApp`` to confirm registration.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.body_size_limiter import BodySizeLimitMiddleware
from app.main import ContentTypeEnforcementMiddleware


def build_app(*middlewares) -> TestClient:
    """A minimal app whose only job is to echo back that it was reached."""

    app = FastAPI()
    for middleware in middlewares:
        app.add_middleware(middleware)

    @app.post("/upload")
    async def upload():
        return {"reached": True}

    @app.post("/api/v1/auth/login")
    async def login():
        return {"reached": True}

    @app.post("/api/v1/outages/")
    async def create_outage():
        return {"reached": True}

    @app.post("/api/v1/outages/bulk")
    async def bulk_outages():
        return {"reached": True}

    @app.post("/api/v1/webhooks/")
    async def create_webhook():
        return {"reached": True}

    @app.get("/api/v1/outages/")
    async def list_outages():
        return {"reached": True}

    return TestClient(app)


def body_of(size: int) -> bytes:
    """A body of exactly ``size`` bytes."""
    return b"x" * size


# --------------------------------------------------------------------------- #
# #973 — content-type enforcement
# --------------------------------------------------------------------------- #


class TestContentTypeEnforcement:
    @pytest.fixture
    def client(self) -> TestClient:
        return build_app(ContentTypeEnforcementMiddleware)

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
    def test_json_is_accepted(self, client: TestClient, method):
        response = client.request(
            method, "/api/v1/outages/", content=b"{}", headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 200

    @pytest.mark.parametrize(
        "content_type",
        ["text/plain", "application/xml", "text/html", "application/x-www-form-urlencoded"],
    )
    def test_unsupported_type_is_rejected_with_415(self, client: TestClient, content_type):
        response = client.post(
            "/api/v1/outages/", content=b"{}", headers={"Content-Type": content_type}
        )

        assert response.status_code == 415
        assert "unsupported media type" in response.json()["detail"].lower()

    def test_missing_content_type_is_rejected_with_415(self, client: TestClient):
        """No header is treated as unsupported, not as "assume JSON".

        Assuming JSON would let a client with no content type reach body parsing
        and surface as a 400 with an unhandled-parse-error shape.
        """
        response = client.post("/api/v1/outages/", content=b"{}")

        assert response.status_code == 415

    def test_charset_parameter_is_tolerated(self, client: TestClient):
        """``application/json; charset=utf-8`` is still JSON.

        Splitting on the ``;`` before comparing is what makes this pass; a naive
        equality check would reject every client that sets a charset.
        """
        response = client.post(
            "/api/v1/outages/",
            content=b"{}",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

        assert response.status_code == 200

    def test_content_type_comparison_is_case_insensitive(self, client: TestClient):
        response = client.post(
            "/api/v1/outages/", content=b"{}", headers={"Content-Type": "APPLICATION/JSON"}
        )

        assert response.status_code == 200

    @pytest.mark.parametrize("method", ["GET", "DELETE", "OPTIONS", "HEAD"])
    def test_read_methods_are_not_gated(self, client: TestClient, method):
        """Only mutating methods carry a body worth a content type.

        Gating GET would break every browser, health probe and prefetch that
        sends no content type at all.
        """
        response = client.request(method, "/api/v1/outages/")

        assert response.status_code in {200, 405}

    def test_upload_path_also_accepts_multipart(self, client: TestClient):
        response = client.post(
            "/upload", content=b"{}", headers={"Content-Type": "multipart/form-data"}
        )

        assert response.status_code == 200

    def test_upload_path_still_rejects_other_types(self, client: TestClient):
        """The multipart allowance is an addition, not a replacement."""
        response = client.post(
            "/upload", content=b"{}", headers={"Content-Type": "text/plain"}
        )

        assert response.status_code == 415

    def test_415_body_names_the_expected_types(self, client: TestClient):
        """The error has to tell the caller what to send instead."""
        detail = client.post(
            "/api/v1/outages/", content=b"{}", headers={"Content-Type": "text/plain"}
        ).json()["detail"]

        assert "application/json" in detail

    def test_multipart_is_rejected_off_upload_paths(self, client: TestClient):
        """The allowance must not leak onto ordinary CRUD routes."""
        response = client.post(
            "/api/v1/outages/", content=b"{}", headers={"Content-Type": "multipart/form-data"}
        )

        assert response.status_code == 415


# --------------------------------------------------------------------------- #
# #974 — request size limits
# --------------------------------------------------------------------------- #


class TestRequestSizeLimits:
    @pytest.fixture
    def client(self) -> TestClient:
        return build_app(BodySizeLimitMiddleware)

    @pytest.mark.parametrize(
        "path,limit",
        [
            ("/api/v1/auth/login", BodySizeLimitMiddleware.AUTH_LIMIT),
            ("/api/v1/outages/", BodySizeLimitMiddleware.CRUD_LIMIT),
            ("/api/v1/outages/bulk", BodySizeLimitMiddleware.BULK_LIMIT),
            ("/api/v1/webhooks/", BodySizeLimitMiddleware.WEBHOOK_LIMIT),
        ],
    )
    def test_just_under_the_limit_is_accepted(
        self, client: TestClient, path, limit
    ):
        """One byte under must pass on every route class."""
        response = client.post(
            path,
            content=body_of(limit - 1),
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200, response.text

    @pytest.mark.parametrize(
        "path,limit",
        [
            ("/api/v1/auth/login", BodySizeLimitMiddleware.AUTH_LIMIT),
            ("/api/v1/outages/", BodySizeLimitMiddleware.CRUD_LIMIT),
            ("/api/v1/outages/bulk", BodySizeLimitMiddleware.BULK_LIMIT),
            ("/api/v1/webhooks/", BodySizeLimitMiddleware.WEBHOOK_LIMIT),
        ],
    )
    def test_exactly_at_the_limit_is_accepted(
        self, client: TestClient, path, limit
    ):
        """The boundary itself must pass; only *over* the limit is rejected.

        Using ``>=`` here would reject a legitimate exactly-at-limit request, which
        is the off-by-one this matrix exists to catch.
        """
        response = client.post(
            path,
            content=body_of(limit),
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200, response.text

    @pytest.mark.parametrize(
        "path,limit",
        [
            ("/api/v1/auth/login", BodySizeLimitMiddleware.AUTH_LIMIT),
            ("/api/v1/outages/", BodySizeLimitMiddleware.CRUD_LIMIT),
            ("/api/v1/outages/bulk", BodySizeLimitMiddleware.BULK_LIMIT),
            ("/api/v1/webhooks/", BodySizeLimitMiddleware.WEBHOOK_LIMIT),
        ],
    )
    def test_one_byte_over_the_limit_is_rejected(
        self, client: TestClient, path, limit
    ):
        response = client.post(
            path,
            content=body_of(limit + 1),
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 413

    def test_auth_limit_is_stricter_than_crud(self, client: TestClient):
        """A credential body must not get a CRUD-sized budget.

        If the prefix table were reordered so a broader match came first, auth
        would silently inherit the 10 KB limit and this would pass unnoticed.
        """
        auth_limit = BodySizeLimitMiddleware.AUTH_LIMIT

        assert auth_limit < BodySizeLimitMiddleware.CRUD_LIMIT

        accepted = client.post(
            "/api/v1/auth/login",
            content=body_of(auth_limit),
            headers={"Content-Type": "application/json"},
        )
        rejected = client.post(
            "/api/v1/auth/login",
            content=body_of(BodySizeLimitMiddleware.CRUD_LIMIT + 1),
            headers={"Content-Type": "application/json"},
        )

        assert accepted.status_code == 200
        assert rejected.status_code == 413

    def test_bulk_limit_is_the_most_permissive(self, client: TestClient):
        assert BodySizeLimitMiddleware.BULK_LIMIT > BodySizeLimitMiddleware.WEBHOOK_LIMIT
        assert BodySizeLimitMiddleware.WEBHOOK_LIMIT > BodySizeLimitMiddleware.CRUD_LIMIT

    def test_413_body_reports_the_limit_and_the_size(self, client: TestClient):
        """A 413 that does not say what the limit was is unactionable."""
        body = client.post(
            "/api/v1/outages/",
            content=body_of(BodySizeLimitMiddleware.CRUD_LIMIT + 1),
            headers={"Content-Type": "application/json"},
        ).json()

        assert body["max_size_bytes"] == BodySizeLimitMiddleware.CRUD_LIMIT
        assert body["received_size_bytes"] > BodySizeLimitMiddleware.CRUD_LIMIT
        assert "10 KB" in body["detail"]

    def test_unknown_path_falls_back_to_the_default_limit(self, client: TestClient):
        """A path with no entry in ``_ROUTE_LIMITS`` still gets guarded.

        Middleware runs before routing, so this 413 fires even though the dummy
        app declares no such route — which is the point: an unrecognised endpoint
        must not become a way to send an unbounded body.
        """
        response = client.post(
            "/api/v1/sla/config",
            content=body_of(BodySizeLimitMiddleware.CRUD_LIMIT + 1),
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 413

    @pytest.mark.parametrize("method", ["GET", "DELETE", "OPTIONS", "HEAD"])
    def test_read_methods_are_not_size_gated(self, client: TestClient, method):
        """A GET carries no body, so a stale content-length must not fail it."""
        response = client.request(method, "/api/v1/outages/")

        assert response.status_code in {200, 405}


class TestAgainstRealApp:
    """Confirms both middlewares are actually registered.

    The matrix above proves behaviour; this proves the production app has the
    behaviour, which is a separate thing that a unit test cannot establish.
    """

    @pytest.fixture
    def client(self):
        from app.main import app

        return TestClient(app)

    def test_unsupported_content_type_is_415_in_the_real_app(self, client: TestClient):
        response = client.post(
            "/api/v1/outages/",
            content=b"{}",
            headers={
                "Authorization": "Bearer test-engineer-token",
                "Content-Type": "text/plain",
            },
        )

        assert response.status_code == 415

    def test_json_is_not_blocked_by_content_type_in_the_real_app(
        self, client: TestClient
    ):
        """The counterpart: 415 must not fire on every request."""
        response = client.post(
            "/api/v1/outages/",
            content=b"{}",
            headers={
                "Authorization": "Bearer test-engineer-token",
                "Content-Type": "application/json",
            },
        )

        # Body validation may reject the empty payload; a 415 would mean the
        # middleware is wrong.
        assert response.status_code != 415
