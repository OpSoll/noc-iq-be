"""Tests for the SLA trends endpoint (Issue #506).

``GET /api/v1/analytics/sla-trends`` accepts a configurable ``group_by``
(daily, weekly, monthly) and returns timestamped uptime/breach metric
buckets from an optimized SQL aggregation.
"""
from fastapi.testclient import TestClient

from app.core.security import require_engineer
from app.main import app
from app.repositories.sla_repository import SLARepository


def _override_engineer():
    app.dependency_overrides[require_engineer] = lambda: {"user_id": "test", "role": "engineer"}


def test_sla_trends_maps_group_by_to_bucket(client: TestClient, monkeypatch):
    captured: dict = {}

    def fake_aggregate_trends(self, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(SLARepository, "aggregate_trends", fake_aggregate_trends)
    _override_engineer()
    try:
        response = client.get("/api/v1/analytics/sla-trends?group_by=daily")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert response.json() == []
    assert captured.get("bucket") == "day"


def test_sla_trends_weekly_and_monthly_mapping(client: TestClient, monkeypatch):
    buckets: list[str] = []

    def fake_aggregate_trends(self, **kwargs):
        buckets.append(kwargs.get("bucket"))
        return []

    monkeypatch.setattr(SLARepository, "aggregate_trends", fake_aggregate_trends)
    _override_engineer()
    try:
        client.get("/api/v1/analytics/sla-trends?group_by=weekly")
        client.get("/api/v1/analytics/sla-trends?group_by=monthly")
    finally:
        app.dependency_overrides.clear()

    assert buckets == ["week", "month"]


def test_sla_trends_rejects_invalid_grouping(client: TestClient):
    _override_engineer()
    try:
        response = client.get("/api/v1/analytics/sla-trends?group_by=hourly")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_sla_trends_requires_engineer(client: TestClient):
    response = client.get("/api/v1/analytics/sla-trends?group_by=daily")
    assert response.status_code in {401, 403}
