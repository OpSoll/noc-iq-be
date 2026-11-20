"""Tests for the SLA rating tier listing endpoint (#882)."""

from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer test-engineer-token"}
URL = "/api/v1/sla/ratings"


def test_returns_ordered_tiers_with_descriptions(client: TestClient):
    response = client.get(URL, headers=AUTH)

    assert response.status_code == 200
    tiers = response.json()
    assert [tier["tier"] for tier in tiers] == ["tier_1", "tier_2", "tier_3"]
    assert all(tier["description"] for tier in tiers)
    assert all(tier["availability_threshold"] > 0 for tier in tiers)


def test_thresholds_are_ordered_strictest_first(client: TestClient):
    thresholds = [tier["availability_threshold"] for tier in client.get(URL, headers=AUTH).json()]

    assert thresholds == sorted(thresholds, reverse=True)


def test_requires_authentication(client: TestClient):
    assert client.get(URL).status_code in {401, 403}
