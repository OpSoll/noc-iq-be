"""
Issue #500 – Pagination parameters and max limit guards on /api/v1/outages.

Verifies:
- ``limit`` defaults to 50 and is capped at 100 (422 beyond the cap).
- ``offset`` defaults to 0 and cannot be negative.
- The ``X-Total-Count`` response header exposes the total matching count.
- Offset-based paging returns bounded pages.
"""

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.enums import OutageStatus, Severity
from app.models.outage_dto import OutageCreate
from app.repositories.outage_repository import OutageRepository

AUTH = {"Authorization": "Bearer test-engineer-token"}


def _seed_outages(db: Session, prefix: str, count: int) -> list[str]:
    repo = OutageRepository(db)
    ids: list[str] = []
    for i in range(count):
        oid = f"out-{prefix}-{i:03d}"
        repo.create(
            OutageCreate(
                id=oid,
                site_name=f"Site {prefix} {i:03d}",
                site_id=f"site-{prefix}-{i:03d}",
                severity=Severity.high,
                status=OutageStatus.open,
                detected_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).replace(minute=i % 60),
                description=f"Pagination seed {prefix} row {i}",
                affected_services=["service1"],
            )
        )
        ids.append(oid)
    return ids


class TestPaginationBounds:
    def test_default_limit_is_50(self, client: TestClient, db: Session):
        prefix = uuid.uuid4().hex[:8]
        _seed_outages(db, prefix, 60)

        response = client.get(f"/api/v1/outages/?search={prefix}", headers=AUTH)

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 50
        assert data["limit"] == 50
        assert data["offset"] == 0
        assert data["total"] == 60
        assert response.headers["X-Total-Count"] == "60"

    def test_limit_capped_at_100(self, client: TestClient, db: Session):
        response = client.get("/api/v1/outages/?limit=101", headers=AUTH)
        assert response.status_code == 422

    def test_limit_zero_rejected(self, client: TestClient, db: Session):
        response = client.get("/api/v1/outages/?limit=0", headers=AUTH)
        assert response.status_code == 422

    def test_negative_offset_rejected(self, client: TestClient, db: Session):
        response = client.get("/api/v1/outages/?offset=-1", headers=AUTH)
        assert response.status_code == 422

    def test_offset_pages_forward(self, client: TestClient, db: Session):
        prefix = uuid.uuid4().hex[:8]
        ids = _seed_outages(db, prefix, 25)

        page1 = client.get(f"/api/v1/outages/?search={prefix}&limit=10&offset=0", headers=AUTH)
        page3 = client.get(f"/api/v1/outages/?search={prefix}&limit=10&offset=20", headers=AUTH)

        assert page1.status_code == 200
        assert page3.status_code == 200
        page1_ids = {item["id"] for item in page1.json()["items"]}
        page3_ids = {item["id"] for item in page3.json()["items"]}
        assert len(page1_ids) == 10
        assert len(page3_ids) == 5
        assert page1_ids.isdisjoint(page3_ids)
        assert page3.json()["total"] == 25
        assert page3.headers["X-Total-Count"] == "25"
        assert page3.json()["offset"] == 20
        assert page3.json()["limit"] == 10

    def test_offset_past_end_returns_empty_page(self, client: TestClient, db: Session):
        prefix = uuid.uuid4().hex[:8]
        _seed_outages(db, prefix, 5)

        response = client.get(f"/api/v1/outages/?search={prefix}&limit=10&offset=100", headers=AUTH)

        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 5
        assert response.headers["X-Total-Count"] == "5"
