"""Tests for the subscribable webhook event listing endpoint (#884)."""

from fastapi.testclient import TestClient

from app.models.webhook import WebhookEvent

ADMIN_AUTH = {"Authorization": "Bearer test-admin-token"}
URL = "/api/v1/webhooks/event-types"


def test_returns_every_supported_event(client: TestClient):
    response = client.get(URL, headers=ADMIN_AUTH)

    assert response.status_code == 200
    assert response.json() == [event.value for event in WebhookEvent]


def test_includes_sla_events(client: TestClient):
    events = client.get(URL, headers=ADMIN_AUTH).json()

    assert "sla.violation" in events
    assert "sla.resolved" in events
    assert "sla.warning" in events


def test_requires_admin(client: TestClient):
    assert client.get(URL).status_code in {401, 403}
    engineer = {"Authorization": "Bearer test-engineer-token"}
    assert client.get(URL, headers=engineer).status_code == 403
