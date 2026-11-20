"""Tests for the distinct outage site id lookup endpoint (#885)."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.enums import OutageStatus, Severity
from app.models.outage_dto import OutageCreate
from app.repositories.outage_repository import OutageRepository

AUTH = {"Authorization": "Bearer test-engineer-token"}
URL = "/api/v1/outages/site-ids"


@pytest.fixture
def seeded(db: Session):
    repo = OutageRepository(db)
    for index, site_id in enumerate(["site-alpha", "site-beta", "site-alpha", "site-gamma"]):
        repo.create(
            OutageCreate(
                id=f"site-lookup-{index}",
                site_name=f"Site {index}",
                site_id=site_id,
                severity=Severity.low,
                status=OutageStatus.open,
                detected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                description="Site id seed",
                affected_services=["dns"],
            )
        )
    db.commit()


def test_returns_distinct_site_ids_sorted(client: TestClient, seeded):
    response = client.get(URL, headers=AUTH)

    assert response.status_code == 200
    assert response.json() == ["site-alpha", "site-beta", "site-gamma"]


def test_prefix_filter_narrows_the_result(client: TestClient, seeded):
    assert client.get(URL, headers=AUTH, params={"prefix": "site-a"}).json() == ["site-alpha"]
    assert client.get(URL, headers=AUTH, params={"prefix": "nope"}).json() == []


def test_empty_when_no_outages_exist(client: TestClient):
    assert client.get(URL, headers=AUTH).json() == []


def test_requires_authentication(client: TestClient, seeded):
    assert client.get(URL).status_code in {401, 403}
