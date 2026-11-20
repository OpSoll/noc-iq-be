"""Tests for the distinct outage assignee lookup endpoint (#886)."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.enums import OutageStatus, Severity
from app.models.outage_dto import OutageCreate
from app.repositories.outage_repository import OutageRepository

AUTH = {"Authorization": "Bearer test-engineer-token"}
URL = "/api/v1/outages/assignees"


@pytest.fixture
def seeded(db: Session):
    repo = OutageRepository(db)
    for index, assignee in enumerate(["zoe", None, "amir", "zoe", ""]):
        repo.create(
            OutageCreate(
                id=f"assignee-{index}",
                site_name=f"Site {index}",
                site_id=f"site-{index}",
                severity=Severity.low,
                status=OutageStatus.open,
                detected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                description="Assignee seed",
                affected_services=["dns"],
                assigned_to=assignee,
            )
        )
    db.commit()


def test_returns_distinct_assignees_alphabetically(client: TestClient, seeded):
    response = client.get(URL, headers=AUTH)

    assert response.status_code == 200
    assert response.json() == ["amir", "zoe"]


def test_excludes_null_and_empty_assignees(client: TestClient, seeded):
    assignees = client.get(URL, headers=AUTH).json()

    assert None not in assignees
    assert "" not in assignees


def test_empty_when_no_assignees_exist(client: TestClient):
    assert client.get(URL, headers=AUTH).json() == []


def test_requires_authentication(client: TestClient, seeded):
    assert client.get(URL).status_code in {401, 403}
