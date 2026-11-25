"""Tests for pagination on the SLA disputes list endpoint (#889)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, insert
from sqlalchemy.orm import Session

from app.models.orm.outage import OutageORM
from app.models.orm.sla import SLAResultORM
from app.models.sla_dispute import SLADispute

AUTH = {"Authorization": "Bearer test-engineer-token"}
URL = "/api/v1/sla/disputes"

SEED_COUNT = 5


@pytest.fixture
def seeded(db: Session):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(SEED_COUNT):
        outage = OutageORM(
            id=f"out-page-{uuid.uuid4().hex[:10]}",
            site_name="Site A",
            site_id="site_1",
            severity="high",
            status="resolved",
            detected_at=base,
            description="Dispute pagination seed",
            affected_services=["4G"],
            mttr_minutes=120,
        )
        db.add(outage)
        db.flush()

        sla_result = SLAResultORM(
            outage_id=outage.id,
            status="violated",
            mttr_minutes=120,
            threshold_minutes=60,
            amount=500.0,
            payment_type="penalty",
            rating="poor",
            is_latest=True,
        )
        db.add(sla_result)
        db.flush()

        db.execute(
            insert(SLADispute.__table__).values(
                sla_result_id=sla_result.id,
                baseline_sla_result_id=sla_result.id,
                sla_id=sla_result.id,
                reason="Seeded for pagination test",
                flagged_by="ops-operator",
                dispute_reason="Seeded for pagination test",
                flagged_at=base + timedelta(minutes=index),
            )
        )
    db.commit()
    return db


def total_disputes(db: Session) -> int:
    return db.query(func.count(SLADispute.id)).scalar()


def test_returns_a_bounded_page_with_total(client: TestClient, seeded):
    response = client.get(URL, headers=AUTH, params={"page": 1, "page_size": 2})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) == 2
    assert body["total"] == total_disputes(seeded)


def test_page_two_returns_different_rows(client: TestClient, seeded):
    first = client.get(URL, headers=AUTH, params={"page": 1, "page_size": 2}).json()
    second = client.get(URL, headers=AUTH, params={"page": 2, "page_size": 2}).json()

    first_ids = {item["id"] for item in first["items"]}
    second_ids = {item["id"] for item in second["items"]}
    assert first_ids.isdisjoint(second_ids)


def test_page_beyond_the_end_is_empty(client: TestClient, seeded):
    body = client.get(URL, headers=AUTH, params={"page": 999, "page_size": 2}).json()

    assert body["items"] == []
    assert body["total"] == total_disputes(seeded)


def test_page_size_is_bounded(client: TestClient, seeded):
    assert client.get(URL, headers=AUTH, params={"page_size": 101}).status_code == 422
    assert client.get(URL, headers=AUTH, params={"page": 0}).status_code == 422


def test_total_respects_the_status_filter(client: TestClient, seeded):
    body = client.get(URL, headers=AUTH, params={"status": "rejected"}).json()

    assert body["total"] == 0
    assert body["items"] == []
