"""Tests for filtering outages by affected service (#887)."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.enums import OutageStatus, Severity
from app.models.outage_dto import OutageCreate
from app.repositories.outage_repository import OutageRepository

AUTH = {"Authorization": "Bearer test-engineer-token"}
LIST_URL = "/api/v1/outages/"
EXPORT_URL = "/api/v1/outages/export"

SEED = [
    ("svc-dns-open", ["dns"], OutageStatus.open),
    ("svc-dns-resolved", ["dns", "web"], OutageStatus.resolved),
    ("svc-web-open", ["web"], OutageStatus.open),
    ("svc-webhook-open", ["webhook"], OutageStatus.open),
    ("svc-vpn-open", ["vpn"], OutageStatus.open),
]


@pytest.fixture
def seeded(db: Session):
    repo = OutageRepository(db)
    for index, (outage_id, services, status) in enumerate(SEED):
        repo.create(
            OutageCreate(
                id=outage_id,
                site_name=f"Site {index}",
                site_id=f"site-{index}",
                severity=Severity.low,
                status=status,
                detected_at=datetime(2026, 1, 1, 0, index, tzinfo=timezone.utc),
                description="Service seed",
                affected_services=services,
            )
        )
    db.commit()


def listed_ids(client: TestClient, **params) -> list[str]:
    response = client.get(LIST_URL, headers=AUTH, params=params)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def test_filters_by_entry_not_substring(client: TestClient, seeded):
    # "webhook" is a different service from "web" and must not match it.
    assert set(listed_ids(client, affected_service="web")) == {
        "svc-web-open",
        "svc-dns-resolved",
    }
    assert listed_ids(client, affected_service="webhook") == ["svc-webhook-open"]


def test_unknown_service_returns_no_rows(client: TestClient, seeded):
    response = client.get(LIST_URL, headers=AUTH, params={"affected_service": "nope"})

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_composes_with_status_filter(client: TestClient, seeded):
    assert listed_ids(client, affected_service="dns", status="open") == ["svc-dns-open"]


def test_export_reflects_the_filter(client: TestClient, seeded):
    response = client.get(
        EXPORT_URL,
        headers=AUTH,
        params={"affected_service": "dns", "format": "json"},
    )

    assert response.status_code == 200
    ids = {row["id"] for row in response.json()}
    assert ids == {"svc-dns-open", "svc-dns-resolved"}
