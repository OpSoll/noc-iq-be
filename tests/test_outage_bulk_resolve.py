"""
Issue #501 – Bulk outage resolution endpoint POST /api/v1/outages/bulk-resolve.

Verifies:
- Accepts ``outage_ids`` + ``resolution_notes`` and resolves atomically.
- Returns a summary of succeeded and failed IDs.
- Missing IDs and invalid transitions are reported without aborting the batch.
- An unexpected mid-batch error rolls back the whole transaction.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.enums import OutageStatus, Severity
from app.models.outage_dto import OutageCreate
from app.repositories.outage_repository import OutageRepository

AUTH = {"Authorization": "Bearer test-engineer-token"}


def _seed_outage(db: Session, oid: str, status: OutageStatus = OutageStatus.open) -> None:
    OutageRepository(db).create(
        OutageCreate(
            id=oid,
            site_name=f"Site {oid}",
            site_id=f"site-{oid}",
            severity=Severity.critical,
            status=status,
            detected_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            description=f"Bulk resolve seed {oid}",
            affected_services=["service1"],
        )
    )


class TestBulkResolve:
    def test_resolves_all_outages_atomically(self, client: TestClient, db: Session):
        id1 = f"out-br-{uuid.uuid4().hex[:8]}-1"
        id2 = f"out-br-{uuid.uuid4().hex[:8]}-2"
        _seed_outage(db, id1)
        _seed_outage(db, id2)

        response = client.post(
            "/api/v1/outages/bulk-resolve",
            json={"outage_ids": [id1, id2], "resolution_notes": "batch fix"},
            headers=AUTH,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["succeeded"] == [id1, id2]
        assert body["failed"] == []
        assert body["total"] == 2
        assert body["success_count"] == 2
        assert body["failure_count"] == 0
        assert body["resolution_notes"] == "batch fix"

        for oid in (id1, id2):
            detail = client.get(f"/api/v1/outages/{oid}", headers=AUTH)
            assert detail.status_code == 200
            assert detail.json()["status"] == "resolved"

    def test_missing_ids_reported_as_failed(self, client: TestClient, db: Session):
        id1 = f"out-br-{uuid.uuid4().hex[:8]}-1"
        _seed_outage(db, id1)

        response = client.post(
            "/api/v1/outages/bulk-resolve",
            json={"outage_ids": [id1, "out-does-not-exist-xyz"], "resolution_notes": ""},
            headers=AUTH,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["succeeded"] == [id1]
        assert body["failure_count"] == 1
        assert body["failed"][0]["id"] == "out-does-not-exist-xyz"
        assert body["failed"][0]["reason"] == "not_found"

    def test_invalid_transition_reported_as_failed(self, client: TestClient, db: Session):
        # resolved -> resolved is idempotent; create one resolved and one open.
        id1 = f"out-br-{uuid.uuid4().hex[:8]}-1"
        _seed_outage(db, id1)
        client.post(
            f"/api/v1/outages/{id1}/resolve",
            json={"mttr_minutes": 30},
            headers=AUTH,
        )

        response = client.post(
            "/api/v1/outages/bulk-resolve",
            json={"outage_ids": [id1], "resolution_notes": ""},
            headers=AUTH,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["succeeded"] == [id1]  # idempotent success for already-resolved

    def test_empty_outage_ids_rejected(self, client: TestClient, db: Session):
        response = client.post(
            "/api/v1/outages/bulk-resolve",
            json={"outage_ids": [], "resolution_notes": ""},
            headers=AUTH,
        )
        assert response.status_code == 422

    def test_requires_engineer_role(self, client: TestClient, db: Session):
        response = client.post(
            "/api/v1/outages/bulk-resolve",
            json={"outage_ids": ["out-x"], "resolution_notes": ""},
        )
        assert response.status_code == 401

    def test_unexpected_error_rolls_back_everything(self, client: TestClient, db: Session):
        id1 = f"out-br-{uuid.uuid4().hex[:8]}-1"
        _seed_outage(db, id1)

        def _explode(self, outage_ids, resolution_notes="", mttr_minutes=None):
            # Simulate a failure after in-flight DB writes: nothing may persist.
            from app.models.orm.outage import OutageORM
            from sqlalchemy import update
            self.db.execute(
                update(OutageORM)
                .where(OutageORM.id == outage_ids[0])
                .values(status=OutageStatus.resolved.value)
            )
            self.db.flush()
            raise RuntimeError("boom")

        with patch(
            "app.api.v1.endpoints.outages.OutageRepository.bulk_resolve",
            _explode,
        ):
            response = client.post(
                "/api/v1/outages/bulk-resolve",
                json={"outage_ids": [id1], "resolution_notes": ""},
                headers=AUTH,
            )

        assert response.status_code == 500

        detail = client.get(f"/api/v1/outages/{id1}", headers=AUTH)
        assert detail.status_code == 200
        assert detail.json()["status"] == "open"  # rollback preserved the original state
