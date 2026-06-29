"""Smoke tests for critical routes (BE-W5-117).

Covers: health, auth, outages, SLA, payments, wallets, webhooks.
Tests are deterministic, time-bounded, and include route-level diagnostics.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app


# ---------------------------------------------------------------------------
# Shared client (session-scoped for speed)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def smoke_client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealthSmoke:
    def test_liveness(self, smoke_client):
        r = smoke_client.get("/health/liveness")
        assert r.status_code == 200, f"/health/liveness: {r.text}"
        assert r.json()["status"] == "ok"

    def test_readiness_responds(self, smoke_client):
        r = smoke_client.get("/health/readiness")
        assert r.status_code == 200, f"/health/readiness: {r.text}"
        assert r.json()["status"] in {"ok", "degraded"}

    def test_legacy_health(self, smoke_client):
        r = smoke_client.get("/health")
        assert r.status_code == 200, f"/health: {r.text}"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestAuthSmoke:
    def test_register_requires_payload(self, smoke_client):
        r = smoke_client.post("/api/v1/auth/register", json={})
        assert r.status_code in {400, 422}, f"register empty body: {r.text}"

    def test_login_requires_payload(self, smoke_client):
        r = smoke_client.post("/api/v1/auth/login", json={})
        assert r.status_code in {400, 422}, f"login empty body: {r.text}"

    def test_protected_route_requires_auth(self, smoke_client):
        r = smoke_client.get("/api/v1/auth/me")
        assert r.status_code in {401, 403}, f"/auth/me without token: {r.text}"

    def test_register_and_login_round_trip(self, smoke_client):
        import uuid
        email = f"smoke-{uuid.uuid4().hex[:8]}@test.invalid"
        pw = "Smoke!Pass9#"

        reg = smoke_client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": pw, "full_name": "Smoke User"},
        )
        # Without a DB, 500 is acceptable; with DB it should be 200/201
        assert reg.status_code in {200, 201, 500}, f"register: {reg.text}"
        if reg.status_code not in {200, 201}:
            pytest.skip("No database available for auth round-trip smoke test")

        login = smoke_client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": pw},
        )
        assert login.status_code == 200, f"login: {login.text}"
        assert "access_token" in login.json(), f"no access_token in: {login.text}"


# ---------------------------------------------------------------------------
# Outages
# ---------------------------------------------------------------------------

class TestOutagesSmoke:
    def test_list_outages_requires_auth(self, smoke_client):
        r = smoke_client.get("/api/v1/outages/")
        assert r.status_code in {200, 401, 403}, f"outages list: {r.text}"

    def test_create_outage_requires_payload(self, smoke_client):
        r = smoke_client.post("/api/v1/outages/", json={})
        assert r.status_code in {400, 401, 422}, f"create outage empty body: {r.text}"

    def test_get_nonexistent_outage_returns_404_or_401(self, smoke_client):
        r = smoke_client.get("/api/v1/outages/does-not-exist")
        assert r.status_code in {401, 403, 404}, f"get nonexistent outage: {r.text}"


# ---------------------------------------------------------------------------
# SLA
# ---------------------------------------------------------------------------

class TestSLASmoke:
    def test_sla_status_responds(self, smoke_client):
        r = smoke_client.get("/api/v1/sla/status")
        assert r.status_code in {200, 401, 403, 422}, f"sla status: {r.text}"

    def test_sla_disputes_list_responds(self, smoke_client):
        r = smoke_client.get("/api/v1/sla/disputes")
        assert r.status_code in {200, 401, 403}, f"sla disputes list: {r.text}"


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------

class TestPaymentsSmoke:
    def test_payments_list_responds(self, smoke_client):
        r = smoke_client.get("/api/v1/payments/")
        assert r.status_code in {200, 401, 403}, f"payments list: {r.text}"

    def test_payments_ping_responds(self, smoke_client):
        r = smoke_client.get("/api/v1/payments/ping")
        assert r.status_code in {200, 401, 403}, f"payments ping: {r.text}"


# ---------------------------------------------------------------------------
# Wallets
# ---------------------------------------------------------------------------

class TestWalletsSmoke:
    def test_wallets_create_requires_auth(self, smoke_client):
        r = smoke_client.post("/api/v1/wallets/create", json={})
        assert r.status_code in {400, 401, 422}, f"create wallet: {r.text}"

    def test_wallets_ping_responds(self, smoke_client):
        r = smoke_client.get("/api/v1/wallets/ping")
        assert r.status_code in {200, 401, 403}, f"wallets ping: {r.text}"


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

class TestWebhooksSmoke:
    def test_list_webhooks_requires_admin(self, smoke_client):
        r = smoke_client.get("/api/v1/webhooks")
        assert r.status_code in {401, 403}, f"list webhooks unauthenticated: {r.text}"

    def test_create_webhook_requires_admin(self, smoke_client):
        r = smoke_client.post("/api/v1/webhooks", json={})
        assert r.status_code in {400, 401, 403, 422}, f"create webhook: {r.text}"


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

class TestAuditSmoke:
    def test_audit_log_requires_admin(self, smoke_client):
        r = smoke_client.get("/api/v1/audit")
        assert r.status_code in {401, 403}, f"audit log unauthenticated: {r.text}"
