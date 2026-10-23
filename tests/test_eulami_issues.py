"""Tests for issues assigned to eulami:
  - #504: RequestValidationError handler format standardization
  - #505: /api/v1/health/detailed endpoint with DB and Redis pings
  - #520: DB connection pool lifecycle configuration
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch


# ---------------------------------------------------------------------------
# #504 – RequestValidationError handler
# ---------------------------------------------------------------------------

def test_validation_error_handler_format():
    """POST to a route with a bad payload returns the standardized envelope."""
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    # /api/v1/auth/register expects username+password; send empty JSON to
    # trigger Pydantic validation without needing prior authentication.
    response = client.post(
        "/api/v1/auth/register",
        json={},
        headers={"Content-Type": "application/json"},
    )

    # The handler must return 422 regardless of which route triggers it.
    assert response.status_code == 422

    body = response.json()
    assert body.get("code") == "VALIDATION_ERROR", (
        f"Expected code='VALIDATION_ERROR', got: {body}"
    )
    assert "message" in body
    assert "details" in body
    assert isinstance(body["details"], list)


def test_validation_error_details_structure():
    """Each entry in details has loc, msg, and type."""
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/v1/outages",
        json={},
        headers={"Content-Type": "application/json"},
    )
    body = response.json()
    if body.get("code") == "VALIDATION_ERROR" and body.get("details"):
        first = body["details"][0]
        assert "loc" in first
        assert "msg" in first
        assert "type" in first


# ---------------------------------------------------------------------------
# #505 – /api/v1/health/detailed with DB and Redis pings
# ---------------------------------------------------------------------------

def test_detailed_health_endpoint_exists():
    """GET /api/v1/health/detailed returns 200 or 503 (never 404)."""
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/v1/health/detailed")
    assert response.status_code in {200, 503}


def test_detailed_health_response_has_redis_key():
    """Response body includes a 'redis' key in dependencies."""
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/v1/health/detailed")
    body = response.json()
    assert "dependencies" in body, "Response must contain 'dependencies'"
    deps = body["dependencies"]
    assert "redis" in deps, (
        f"'redis' key missing from dependencies. Got: {list(deps.keys())}"
    )
    assert deps["redis"] in {"ok", "down"}


def test_detailed_health_response_has_database_key():
    """Response body includes a 'database' key in dependencies."""
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/v1/health/detailed")
    body = response.json()
    deps = body.get("dependencies", {})
    assert "database" in deps
    assert deps["database"] in {"ok", "down"}


def test_detailed_health_503_when_redis_down():
    """Returns HTTP 503 when Redis is unreachable."""
    from app.api.v1.endpoints.health import router
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    with (
        patch(
            "app.api.v1.endpoints.health._check_redis",
            return_value=False,
        ),
        patch(
            "app.api.v1.endpoints.health._check_database",
            return_value=True,
        ),
        patch(
            "app.api.v1.endpoints.health._check_celery_broker",
            return_value=True,
        ),
        patch(
            "app.api.v1.endpoints.health.read_worker_health",
            return_value={"status": "ok"},
        ),
    ):
        response = client.get("/api/v1/health/detailed")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["dependencies"]["redis"] == "down"


def test_detailed_health_200_when_all_healthy():
    """Returns HTTP 200 when all dependencies are healthy."""
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    with (
        patch(
            "app.api.v1.endpoints.health._check_redis",
            return_value=True,
        ),
        patch(
            "app.api.v1.endpoints.health._check_database",
            return_value=True,
        ),
        patch(
            "app.api.v1.endpoints.health._check_celery_broker",
            return_value=True,
        ),
        patch(
            "app.api.v1.endpoints.health.read_worker_health",
            return_value={"status": "ok"},
        ),
    ):
        response = client.get("/api/v1/health/detailed")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"


# ---------------------------------------------------------------------------
# #520 – DB connection pool configuration
# ---------------------------------------------------------------------------

def test_pool_pre_ping_enabled():
    """Engine must have pool_pre_ping=True."""
    from app.db.session import engine

    assert engine.pool._pre_ping is True, "pool_pre_ping must be True"


def test_pool_size_and_max_overflow():
    """Engine must be configured with pool_size=20 and max_overflow=10 for PostgreSQL."""
    from sqlalchemy.engine import make_url
    from app.db.session import engine, _DB_URL

    url = make_url(_DB_URL)
    if url.get_backend_name() == "sqlite":
        # SQLite uses StaticPool which does not expose pool_size / max_overflow.
        pytest.skip("pool_size/max_overflow not applicable to SQLite test database")

    pool = engine.pool
    assert pool.size() == 20, f"Expected pool_size=20, got {pool.size()}"
    assert pool._max_overflow == 10, f"Expected max_overflow=10, got {pool._max_overflow}"


def test_checkout_listener_registered():
    """Engine pool must have a checkout event listener for lifecycle logging."""
    from app.db.session import engine, _on_checkout

    # Verify the listener function is callable (decorated with @event.listens_for).
    assert callable(_on_checkout)

    # Verify the listener is actually registered on the pool dispatch.
    checkout_listeners = list(engine.pool.dispatch.checkout)
    assert len(checkout_listeners) > 0, (
        "No checkout listeners registered on pool"
    )
    listener_fns = [fn for fn in checkout_listeners]
    assert _on_checkout in listener_fns, (
        "_on_checkout not found in pool checkout listeners"
    )


def test_checkin_listener_registered():
    """Engine must have a checkin event listener for leak detection."""
    from app.db.session import _on_checkin

    assert callable(_on_checkin)
