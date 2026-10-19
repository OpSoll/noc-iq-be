"""Tests for the outage count endpoint and the soft-delete listing contract.

#964 — ``GET /api/v1/outages/count`` returns only how many outages match, so a
client can size a result set without transferring a page of rows.

#980 — the soft-delete contract that endpoint inherits: the default list excludes
deleted outages, ``/deleted`` includes them, and a restore returns one to the
default list.

Most assertions here compare the count against ``total`` from the listing rather
than against a literal. Agreement between the two is the property that matters:
a count that included soft-deleted rows, or matched search case-sensitively,
would leave a paginator computing page counts from rows the client can never
load, and nothing would fail loudly.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.enums import OutageStatus, Severity
from app.models.outage_dto import OutageCreate
from app.repositories.outage_repository import OutageRepository

AUTH = {"Authorization": "Bearer test-engineer-token"}
COUNT_URL = "/api/v1/outages/count"
LIST_URL = "/api/v1/outages/"
DELETED_URL = "/api/v1/outages/deleted"

# One row per severity, split across both statuses, so a filter that is ignored
# or applied with the wrong predicate cannot pass by accident.
SEED = [
    ("cnt-crit-open", Severity.critical, OutageStatus.open, 0),
    ("cnt-high-open", Severity.high, OutageStatus.open, 1),
    ("cnt-med-open", Severity.medium, OutageStatus.open, 2),
    ("cnt-low-resolved", Severity.low, OutageStatus.resolved, 3),
    ("cnt-crit-resolved", Severity.critical, OutageStatus.resolved, 4),
]


@pytest.fixture
def seeded(db: Session):
    """Seed five outages and return their ids."""
    repo = OutageRepository(db)
    ids = []
    for outage_id, severity, status, minute in SEED:
        repo.create(
            OutageCreate(
                id=outage_id,
                site_name=f"Site {outage_id}",
                site_id=f"site-{outage_id}",
                severity=severity,
                status=status,
                detected_at=datetime(2026, 1, 1, 0, minute, tzinfo=timezone.utc),
                description=f"Count seed {outage_id}",
                affected_services=["service1"],
            )
        )
        ids.append(outage_id)
    db.commit()
    return ids


def mark_deleted(db: Session, outage_id: str, deleted: bool) -> None:
    """Flip the soft-delete flag directly, mirroring the ORM column contract."""
    from app.models.orm.outage import Outage as OutageORM

    db.query(OutageORM).filter(OutageORM.id == outage_id).update(
        {"is_deleted": deleted, "deleted_at": datetime.now(timezone.utc) if deleted else None}
    )
    db.commit()


def list_total(client: TestClient, **params) -> int:
    response = client.get(LIST_URL, headers=AUTH, params=params)
    assert response.status_code == 200, response.text
    return response.json()["total"]


def get_count(client: TestClient, **params) -> int:
    response = client.get(COUNT_URL, headers=AUTH, params=params)
    assert response.status_code == 200, response.text
    return response.json()["count"]


def listed_ids(client: TestClient, url: str = LIST_URL) -> list[str]:
    return [item["id"] for item in client.get(url, headers=AUTH).json()["items"]]


# --------------------------------------------------------------------------- #
# #964 — count endpoint
# --------------------------------------------------------------------------- #


class TestCountEndpointShape:
    def test_returns_only_the_count(self, client: TestClient, seeded):
        response = client.get(COUNT_URL, headers=AUTH)
        assert response.status_code == 200

        body = response.json()
        assert body["count"] == len(SEED)
        # The endpoint exists to avoid transferring rows; returning them too
        # would defeat it.
        assert "items" not in body
        assert "results" not in body
        assert "data" not in body

    def test_requires_authentication(self, client: TestClient, seeded):
        assert client.get(COUNT_URL).status_code in {401, 403}

    def test_empty_table_counts_zero(self, client: TestClient):
        response = client.get(COUNT_URL, headers=AUTH)
        assert response.status_code == 200
        assert response.json()["count"] == 0

    def test_rejects_invalid_severity(self, client: TestClient, seeded):
        assert (
            client.get(COUNT_URL, headers=AUTH, params={"severity": "nope"}).status_code == 422
        )

    def test_rejects_invalid_status(self, client: TestClient, seeded):
        assert (
            client.get(COUNT_URL, headers=AUTH, params={"status": "nope"}).status_code == 422
        )

    def test_is_not_shadowed_by_the_id_route(self, client: TestClient, seeded):
        """/count must not be parsed as an outage id.

        ``GET /{outage_id}`` would happily treat "count" as an id and return 404,
        which would make this endpoint look like it simply does not exist.
        """
        response = client.get(COUNT_URL, headers=AUTH)
        assert response.status_code == 200
        assert response.json()["count"] == len(SEED)


class TestCountRespectsFilters:
    def test_unfiltered_count_matches_listing(self, client: TestClient, seeded):
        assert get_count(client) == list_total(client) == len(SEED)

    def test_severity_filter(self, client: TestClient, seeded):
        assert get_count(client, severity="critical") == 2
        assert get_count(client, severity="high") == 1
        assert get_count(client, severity="medium") == 1
        assert get_count(client, severity="low") == 1

    def test_status_filter(self, client: TestClient, seeded):
        assert get_count(client, status="open") == 3
        assert get_count(client, status="resolved") == 2

    def test_severity_and_status_are_conjunctive(self, client: TestClient, seeded):
        """critical+open is one row; critical+resolved is the other."""
        assert get_count(client, severity="critical", status="open") == 1
        assert get_count(client, severity="critical", status="resolved") == 1
        assert get_count(client, severity="high", status="resolved") == 0

    def test_search_is_case_insensitive(self, client: TestClient, seeded):
        lower = get_count(client, search="site-crit")
        upper = get_count(client, search="SITE-CRIT")
        assert lower == 2
        assert upper == lower

    def test_search_matches_nothing_is_zero_not_an_error(self, client: TestClient, seeded):
        assert get_count(client, search="no-such-site") == 0

    def test_date_range_excludes_older_rows(self, client: TestClient, seeded):
        # All rows are 2026-01-01; a window that starts later excludes them all.
        assert get_count(client, start_date="2026-01-01T00:03:00Z") == 2
        assert get_count(client, start_date="2099-01-01T00:00:00Z") == 0
        assert get_count(client, end_date="2099-01-01T00:00:00Z") == len(SEED)

    @pytest.mark.parametrize("severity", ["critical", "high", "medium", "low"])
    def test_agrees_with_listing_under_severity(self, client: TestClient, seeded, severity):
        assert get_count(
            client, severity=severity
        ) == list_total(client, severity=severity)

    @pytest.mark.parametrize("status", ["open", "resolved"])
    def test_agrees_with_listing_under_status(self, client: TestClient, seeded, status):
        assert get_count(client, status=status) == list_total(client, status=status)

    def test_agrees_with_listing_under_search(self, client: TestClient, seeded):
        assert get_count(client, search="site-crit") == list_total(client, search="site-crit")
        assert get_count(client, search="site-") == list_total(client, search="site-")


# --------------------------------------------------------------------------- #
# #980 — soft-delete listing contract
# --------------------------------------------------------------------------- #


class TestSoftDeleteListing:
    def test_default_list_excludes_deleted(self, client: TestClient, seeded, db: Session):
        mark_deleted(db, "cnt-low-resolved", True)

        ids = listed_ids(client)
        assert "cnt-low-resolved" not in ids
        assert list_total(client) == len(SEED) - 1

    def test_deleted_endpoint_includes_deleted(self, client: TestClient, seeded, db: Session):
        mark_deleted(db, "cnt-low-resolved", True)

        response = client.get(DELETED_URL, headers=AUTH)
        assert response.status_code == 200
        assert response.json()["total"] == 1

    def test_deleted_endpoint_does_not_return_live_rows(self, client: TestClient, seeded, db: Session):
        mark_deleted(db, "cnt-low-resolved", True)

        for item in client.get(DELETED_URL, headers=AUTH).json()["items"]:
            assert item["id"] == "cnt-low-resolved"

    def test_deleted_endpoint_is_empty_when_nothing_is_deleted(self, client: TestClient, seeded):
        assert client.get(DELETED_URL, headers=AUTH).json()["total"] == 0

    def test_restore_returns_the_outage_to_the_default_list(self, client, seeded, db: Session):
        """The round trip: visible -> deleted -> visible again.

        Soft delete is only correct if it is reversible. A row that stayed out of
        the default list after a restore would be lost data rather than an
        archived row, and nothing else would notice.
        """
        assert "cnt-crit-open" in listed_ids(client)

        mark_deleted(db, "cnt-crit-open", True)
        assert "cnt-crit-open" not in listed_ids(client)
        assert "cnt-crit-open" in listed_ids(client, DELETED_URL)

        mark_deleted(db, "cnt-crit-open", False)
        assert "cnt-crit-open" in listed_ids(client)
        assert list_total(client) == len(SEED)

    def test_multiple_deleted_rows_are_all_excluded(self, client, seeded, db: Session):
        mark_deleted(db, "cnt-crit-open", True)
        mark_deleted(db, "cnt-low-resolved", True)

        ids = listed_ids(client)
        assert "cnt-crit-open" not in ids
        assert "cnt-low-resolved" not in ids
        assert client.get(DELETED_URL, headers=AUTH).json()["total"] == 2

    def test_count_agrees_with_default_list_before_and_after_delete(
        self, client, seeded, db: Session
    ):
        """The count endpoint must track the same soft-delete state as the list."""
        assert get_count(client) == list_total(client) == len(SEED)

        mark_deleted(db, "cnt-crit-open", True)

        assert get_count(client) == list_total(client) == len(SEED) - 1

        mark_deleted(db, "cnt-crit-open", False)

        assert get_count(client) == list_total(client) == len(SEED)

    def test_count_under_filters_tracks_soft_delete(self, client, seeded, db: Session):
        mark_deleted(db, "cnt-crit-open", True)

        assert get_count(client, severity="critical") == 1
        assert get_count(client, severity="critical") == list_total(
            client, severity="critical"
        )
