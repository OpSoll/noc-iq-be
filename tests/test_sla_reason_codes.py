"""Tests for the SLA reason code listing endpoint (#881)."""

from fastapi.testclient import TestClient

from app.services.sla.sla_calculator import SLA_REASON_CODES

AUTH = {"Authorization": "Bearer test-engineer-token"}
URL = "/api/v1/sla/reason-codes"


def test_returns_every_calculator_reason_code(client: TestClient):
    response = client.get(URL, headers=AUTH)

    assert response.status_code == 200
    assert [item["code"] for item in response.json()] == list(SLA_REASON_CODES)


def test_every_code_has_a_description(client: TestClient):
    body = client.get(URL, headers=AUTH).json()

    assert all(item["description"] for item in body)


def test_includes_the_penalty_reason_code(client: TestClient):
    codes = {item["code"] for item in client.get(URL, headers=AUTH).json()}

    assert "mttr_exceeded" in codes


def test_requires_authentication(client: TestClient):
    assert client.get(URL).status_code in {401, 403}
