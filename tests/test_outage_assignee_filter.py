"""Tests for filtering outages by assignee (#888)."""

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
    ("asg-amir-open", "amir", OutageStatus.open),
    ("asg-amir-resolved", "amir", OutageStatus.resolved),
    ("asg-zoe-open", "zoe", OutageStatus.open),
    ("asg-unassigned", None, OutageStatus.open),
]


@pytest.fixture
def seeded(db: Session):
    repo = OutageRepository(db)
    for index, (outage_id, assignee, status) in enumerate(SEED):
        repo.create(
            OutageCreate(
                id=outage_id,
                site_name=f"Site {index}",
                site_id=f"site-{index}",
                severity=Severity.low,
                status=status,
                detected_at=datetime(2026, 1, 1, 0, index, tzinfo=timezone.utc),
                description="Assignee filter seed",
                affected_services=["dns"],
                assigned_to=assignee,
            )
        )
    db.commit()


def listed_ids(client: TestClient, **params) -> list[str]:
    response = client.get(LIST_URL, headers=AUTH, params=params)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def test_filters_by_exact_assignee(client: TestClient, seeded):
    assert set(listed_ids(client, assigned_to="amir")) == {
        "asg-amir-open",
        "asg-amir-resolved",
    }


def test_unknown_assignee_returns_no_rows(client: TestClient, seeded):
    assert listed_ids(client, assigned_to="nobody") == []


def test_composes_with_status_and_severity(client: TestClient, seeded):
    assert listed_ids(client, assigned_to="amir", status="open") == ["asg-amir-open"]
    assert listed_ids(client, assigned_to="amir", severity="critical") == []


def test_export_reflects_the_filter(client: TestClient, seeded):
    response = client.get(
        EXPORT_URL, headers=AUTH, params={"assigned_to": "zoe", "format": "json"}
    )

    assert response.status_code == 200
    assert {row["id"] for row in response.json()} == {"asg-zoe-open"}
