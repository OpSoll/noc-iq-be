"""Combined filter and pagination tests for the outage list (#905).

The individual filters are covered elsewhere; these tests exercise the
interactions: filters plus a page window, page boundaries, and empty results.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.enums import OutageStatus, Severity
from app.models.outage_dto import OutageCreate
from app.repositories.outage_repository import OutageRepository

AUTH = {"Authorization": "Bearer test-engineer-token"}
URL = "/api/v1/outages/"

# (id, site_name, severity, status, minute) — two pages worth of rows.
SEED = [
    ("combo-1", "Alpha Core", Severity.critical, OutageStatus.open, 0),
    ("combo-2", "Alpha Edge", Severity.critical, OutageStatus.resolved, 1),
    ("combo-3", "Alpha Core", Severity.high, OutageStatus.open, 2),
    ("combo-4", "Beta Core", Severity.high, OutageStatus.open, 3),
    ("combo-5", "Beta Edge", Severity.medium, OutageStatus.resolved, 4),
]


@pytest.fixture
def seeded(db: Session):
    repo = OutageRepository(db)
    for outage_id, site_name, severity, status, minute in SEED:
        repo.create(
            OutageCreate(
                id=outage_id,
                site_name=site_name,
                site_id=f"site-{outage_id}",
                severity=severity,
                status=status,
                detected_at=datetime(2026, 1, 1, 0, minute, tzinfo=timezone.utc),
                description="Combination seed",
                affected_services=["dns"],
            )
        )
    db.commit()


def query(client: TestClient, **params) -> dict:
    response = client.get(URL, headers=AUTH, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_search_and_severity_and_status_combine(client: TestClient, seeded):
    body = query(client, search="Alpha", severity="critical", status="open")

    assert [item["id"] for item in body["items"]] == ["combo-1"]
    assert body["total"] == 1


def test_combined_filters_with_pagination_window(client: TestClient, seeded):
    first = query(client, search="Alpha", limit=1, offset=0)
    second = query(client, search="Alpha", limit=1, offset=1)

    assert first["total"] == second["total"] == 3
    assert len(first["items"]) == len(second["items"]) == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]


def test_offset_past_the_end_is_empty_with_unchanged_total(client: TestClient, seeded):
    body = query(client, limit=2, offset=50)

    assert body["items"] == []
    assert body["total"] >= 2


def test_filters_applied_before_pagination(client: TestClient, seeded):
    body = query(client, status="open", limit=2, offset=0)

    assert body["total"] == 3
    assert all(item["status"] == "open" for item in body["items"])


def test_empty_result_is_zero_not_an_error(client: TestClient, seeded):
    body = query(client, search="no-such-site", severity="low", limit=10, offset=0)

    assert body["items"] == []
    assert body["total"] == 0


def test_invalid_limit_is_rejected(client: TestClient, seeded):
    assert client.get(URL, headers=AUTH, params={"limit": 0}).status_code == 422
    assert client.get(URL, headers=AUTH, params={"limit": 101}).status_code == 422
