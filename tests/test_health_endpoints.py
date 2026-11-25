from fastapi.testclient import TestClient
from app.main import app


def test_health_liveness_endpoint():
    client = TestClient(app)
    response = client.get("/health/liveness")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "timestamp" in body


def test_health_readiness_endpoint():
    client = TestClient(app)
    response = client.get("/health/readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert body["dependencies"]["database"] in {"ok", "down"}
    assert body["dependencies"]["celery"] in {"ok", "down"}
    assert "timestamp" in body


def test_legacy_health_endpoint_is_liveness_alias():
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_v2_health_contract_and_v1_unchanged():
    from app.core.config import settings

    client = TestClient(app)
    v2 = client.get(f"{settings.API_V2_PREFIX}/health")
    assert v2.status_code == 200
    body = v2.json()
    assert set(body) == {"status", "version", "timestamp"}
    assert body["status"] == "ok"
    assert body["version"] == "v2"

    v1 = client.get("/health")
    assert v1.status_code == 200
    assert v1.json()["status"] == "ok"
    assert v1.json().get("version") != "v2"
