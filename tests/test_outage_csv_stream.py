"""Tests for streaming CSV outage export (Issue #507).

``GET /api/v1/outages/export?format=csv`` must return a streaming CSV
response with ``Content-Disposition: attachment; filename=outages.csv`` and
include all exported outage rows.
"""
import csv
import io
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import require_engineer
from app.main import app
from app.models.orm.outage import OutageORM


def _make_outage(db: Session, outage_id: str) -> OutageORM:
    outage = OutageORM(
        id=outage_id,
        site_name=f"Site {uuid.uuid4().hex[:6]}",
        site_id="site_1",
        severity="high",
        status="open",
        detected_at=datetime.now(timezone.utc),
        description="CSV stream test outage",
        affected_services=["4G"],
    )
    db.add(outage)
    db.commit()
    db.refresh(outage)
    return outage


def test_csv_export_streams_with_attachment_header(client: TestClient, db: Session):
    _make_outage(db, "out-csv-1")

    app.dependency_overrides[require_engineer] = lambda: {"user_id": "test", "role": "engineer"}
    try:
        response = client.get("/api/v1/outages/export?format=csv")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == "attachment; filename=outages.csv"

    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows, "CSV body should contain at least the seeded outage"
    assert any(r["id"] == "out-csv-1" for r in rows)
    assert "site_name" in rows[0]


def test_csv_export_filters_by_status(client: TestClient, db: Session):
    _make_outage(db, "out-csv-open")
    resolved = OutageORM(
        id="out-csv-resolved",
        site_name="Site B",
        site_id="site_2",
        severity="medium",
        status="resolved",
        detected_at=datetime.now(timezone.utc),
        resolved_at=datetime.now(timezone.utc),
        description="Resolved outage",
        affected_services=["4G"],
    )
    db.add(resolved)
    db.commit()

    app.dependency_overrides[require_engineer] = lambda: {"user_id": "test", "role": "engineer"}
    try:
        response = client.get("/api/v1/outages/export?format=csv&status=resolved")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    rows = list(csv.DictReader(io.StringIO(response.text)))
    ids = {r["id"] for r in rows}
    assert "out-csv-resolved" in ids
    assert "out-csv-open" not in ids


def test_json_export_still_works(client: TestClient, db: Session):
    _make_outage(db, "out-json-1")

    app.dependency_overrides[require_engineer] = lambda: {"user_id": "test", "role": "engineer"}
    try:
        response = client.get("/api/v1/outages/export?format=json")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    data = response.json()
    assert isinstance(data, list)
    assert any(o["id"] == "out-json-1" for o in data)
