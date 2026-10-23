"""Tests for outage bulk import: dry-run purity (#972) and duplicate handling (#971).

Both issues concern the same endpoint, ``POST /api/v1/outages/import``, and the
same failure mode: an import that looks correct in its response but has written
something it should not have. Neither had coverage — no test in the suite posted
to ``/import`` at all.

#972 — a dry run must never persist. This is the more dangerous of the two,
because the response of a dry run and a live import are near-identical: both
report ``total_rows``, ``validated`` and per-row outcomes, and only ``mode``
distinguishes them. A dry run that quietly wrote rows would therefore look
correct to any caller checking the response, and would surface later as data
that appeared with no import having been recorded as live.

#971 — duplicates are skipped and reported, not silently overwritten. The
duplicate key is ``(site_name, detected_at, description)``, optionally narrowed by
``site_id`` — deliberately *not* the outage id, so two rows can collide as
duplicates while carrying different ids.
"""

import io
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.enums import OutageStatus, Severity
from app.models.outage_dto import OutageCreate
from app.repositories.outage_repository import OutageRepository

AUTH = {"Authorization": "Bearer test-engineer-token"}
IMPORT_URL = "/api/v1/outages/import"

# One distinct outage. detected_at is fixed so a payload can be re-sent verbatim
# to produce a genuine duplicate rather than an accidental near-miss.
BASE_DETECTED_AT = datetime(2026, 2, 1, 9, 0, tzinfo=timezone.utc)


def outage_row(outage_id: str, **overrides) -> dict:
    """A valid outage payload, shaped like a parsed CSV/JSON row."""
    row = {
        "id": outage_id,
        "site_name": "Frankfurt DC",
        "site_id": "site-fra",
        "severity": "high",
        "status": "open",
        "detected_at": BASE_DETECTED_AT.isoformat(),
        "description": "Elevated latency on the edge tier",
        "affected_services": ["edge"],
    }
    row.update(overrides)
    return row


def import_json(client: TestClient, rows, **params):
    return client.post(
        IMPORT_URL,
        headers=AUTH,
        params=params,
        files={"file": ("outages.json", io.BytesIO(json.dumps(rows).encode()), "application/json")},
    )


def persisted_ids(db: Session) -> set[str]:
    from app.models.orm.outage import Outage as OutageORM

    return {row.id for row in db.query(OutageORM).all()}


@pytest.fixture
def client(db: Session) -> TestClient:
    """A TestClient for the import endpoint.

    The suite already provides a session-scoped ``client`` fixture; this shadows
    it only to make the dependency on this test's ``db`` explicit, since every
    assertion here reads rows written through that session.
    """
    from app.main import app

    return TestClient(app)


# --------------------------------------------------------------------------- #
# #972 — dry run must not persist
# --------------------------------------------------------------------------- #


class TestDryRunPersistsNothing:
    def test_dry_run_writes_no_rows(self, client: TestClient, db: Session):
        response = import_json(
            client, [outage_row("dry-1"), outage_row("dry-2")], dry_run=True
        )
        assert response.status_code == 200, response.text

        assert persisted_ids(db) == set()

    def test_dry_run_writes_nothing_even_when_all_rows_are_valid(self, client: TestClient, db: Session):
        """The strongest form: valid rows are exactly what a live import persists.

        A dry run that only avoided writing invalid rows would pass a test that
        used bad data. This one uses rows a live import would fully persist.
        """
        response = import_json(client, [outage_row("dry-ok")], dry_run=True)
        body = response.json()

        assert body["validated"] == 1
        assert body["error_count"] == 0
        assert persisted_ids(db) == set()

    def test_dry_run_still_returns_validation_results(self, client: TestClient, db: Session):
        """A dry run that validated nothing would be useless."""
        response = import_json(
            client, [outage_row("dry-1"), outage_row("dry-2")], dry_run=True
        )
        body = response.json()

        assert body["mode"] == "dry_run"
        assert body["total_rows"] == 2
        assert body["validated"] == 2
        assert len(body["rows"]) == 2

    def test_dry_run_reports_field_errors(self, client: TestClient, db: Session):
        """Validation runs identically to a live import, including errors."""
        response = import_json(
            client,
            [outage_row("dry-bad", severity="catastrophic")],
            dry_run=True,
        )
        body = response.json()

        assert body["error_count"] == 1
        assert body["errors"][0]["row"] == 0
        assert persisted_ids(db) == set()

    def test_dry_run_counts_are_accurate(self, client: TestClient, db: Session):
        """One valid, one invalid: the counts must add up and persist nothing."""
        response = import_json(
            client,
            [outage_row("dry-good"), outage_row("dry-bad", severity="nope")],
            dry_run=True,
        )
        body = response.json()

        assert body["total_rows"] == 2
        assert body["validated"] == 1
        assert body["error_count"] == 1
        assert body["validated"] + body["error_count"] == body["total_rows"]
        assert persisted_ids(db) == set()

    def test_dry_run_reports_persisted_zero(self, client: TestClient, db: Session):
        response = import_json(client, [outage_row("dry-1")], dry_run=True)

        assert response.json()["persisted"] == 0
        assert persisted_ids(db) == set()

    def test_dry_run_leaves_existing_rows_untouched(self, client: TestClient, db: Session):
        """An existing outage must not be modified by a dry run.

        Re-sending an existing payload is the case where a dry run could do
        damage without adding rows, which a row-count assertion would miss.
        """
        OutageRepository(db).create(
            OutageCreate(
                id="existing-1",
                site_name="Frankfurt DC",
                site_id="site-fra",
                severity=Severity.high,
                status=OutageStatus.open,
                detected_at=BASE_DETECTED_AT,
                description="Elevated latency on the edge tier",
                affected_services=["edge"],
            )
        )
        db.commit()
        before = persisted_ids(db)

        response = import_json(client, [outage_row("existing-1")], dry_run=True)
        assert response.status_code == 200, response.text

        assert persisted_ids(db) == before

    def test_live_import_does_persist(self, client: TestClient, db: Session):
        """The control case, without which the tests above prove nothing.

        If a live import also wrote nothing, every dry-run assertion would pass
        against a broken endpoint.
        """
        response = import_json(client, [outage_row("live-1")])
        assert response.status_code == 200, response.text

        assert response.json()["persisted"] == 1
        assert persisted_ids(db) == {"live-1"}

    def test_dry_run_then_live_import_writes_exactly_once(self, client: TestClient, db: Session):
        """Dry run first, then live: the payload must persist only on the second call."""
        rows = [outage_row("dry-then-live")]

        import_json(client, rows, dry_run=True)
        assert persisted_ids(db) == set()

        import_json(client, rows)
        assert persisted_ids(db) == {"dry-then-live"}


# --------------------------------------------------------------------------- #
# #971 — duplicate detection
# --------------------------------------------------------------------------- #


class TestDuplicateDetection:
    def test_duplicate_rows_are_reported_not_reimported(self, client: TestClient, db: Session):
        """Two identical payloads in one file: the second is a duplicate."""
        rows = [outage_row("dup-a"), outage_row("dup-a")]

        response = import_json(client, rows)
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["persisted"] == 1
        assert len(body["rows"]) == 2

        # Both rows share the duplicate key, so the second is flagged and points
        # at the row that won.
        assert body["rows"][1]["duplicate"] is True
        assert body["rows"][1]["persisted"] is False
        assert body["rows"][1]["existing_id"] == "dup-a"

    def test_non_duplicates_are_all_imported(self, client: TestClient, db: Session):
        """Distinct detected_at makes each row genuinely distinct."""
        rows = [
            outage_row("uniq-1"),
            outage_row("uniq-2", detected_at=datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc).isoformat()),
            outage_row("uniq-3", description="A different failure mode entirely"),
        ]

        response = import_json(client, rows)
        body = response.json()

        assert body["persisted"] == 3
        assert body["error_count"] == 0
        for row in body["rows"]:
            assert row["duplicate"] is False
        assert persisted_ids(db) == {"uniq-1", "uniq-2", "uniq-3"}

    def test_duplicate_of_an_already_stored_outage_is_detected(self, client: TestClient, db: Session):
        """The duplicate is already in the database, not just repeated in the file."""
        import_json(client, [outage_row("already-there")])
        db.commit()

        response = import_json(client, [outage_row("second-attempt")])
        body = response.json()

        assert body["persisted"] == 0
        assert body["rows"][0]["duplicate"] is True
        assert body["rows"][0]["existing_id"] == "already-there"
        # The second id was never created.
        assert persisted_ids(db) == {"already-there"}

    def test_duplicate_count_is_reported(self, client: TestClient, db: Session):
        rows = [outage_row("d-1"), outage_row("d-1"), outage_row("d-2")]

        body = import_json(client, rows).json()

        duplicates = [r for r in body["rows"] if r["duplicate"] is True]
        assert len(duplicates) == 1
        assert body["persisted"] == 2

    def test_dry_run_reports_duplicates_without_persisting(self, client: TestClient, db: Session):
        """Duplicate detection must run on the dry-run path too.

        The dry-run branch calls the same check_duplicate, but the two paths are
        separate code, so this asserts the dry run actually detects them rather
        than reporting everything as new.
        """
        rows = [outage_row("dup-dry"), outage_row("dup-dry")]

        body = import_json(client, rows, dry_run=True).json()

        assert body["rows"][1]["duplicate"] is True
        assert body["rows"][1]["existing_id"] == "dup-dry"
        assert persisted_ids(db) == set()

    def test_different_site_is_not_a_duplicate(self, client: TestClient, db: Session):
        """The duplicate key includes site_id, so the same failure elsewhere is new."""
        rows = [
            outage_row("site-a-1", site_id="site-a", site_name="Frankfurt DC"),
            outage_row("site-b-1", site_id="site-b", site_name="Frankfurt DC"),
        ]

        body = import_json(client, rows).json()

        assert body["persisted"] == 2

    def test_partial_consistency_reports_duplicates_per_row(self, client: TestClient, db: Session):
        """Under partial mode a duplicate is a row outcome, not a failure.

        A duplicate row is not an error, so it must not appear in error_count or
        be treated as a row-level failure.
        """
        rows = [outage_row("p-1"), outage_row("p-1"), outage_row("p-bad", severity="nope")]

        response = import_json(client, rows, consistency="partial")
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["persisted"] == 1
        assert body["error_count"] == 1
        assert body["rows"][1]["duplicate"] is True
        assert persisted_ids(db) == {"p-1"}
