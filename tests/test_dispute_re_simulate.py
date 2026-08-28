"""Tests for the dispute re-simulation endpoint (Issue #510).

``POST /api/v1/disputes/{id}/re-simulate`` dry-runs the Soroban SLA
calculation with updated MTTR values and returns a comparison of the
original vs. simulated penalty amount.
"""
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.models.orm.outage import OutageORM
from app.models.orm.sla import SLAResultORM
from app.models.sla_dispute import SLADispute


def _seed_dispute(db: Session) -> tuple[int, str]:
    outage_id = f"out-rs-{uuid.uuid4().hex[:10]}"
    outage = OutageORM(
        id=outage_id,
        site_name="Site A",
        site_id="site_1",
        severity="high",
        status="resolved",
        detected_at=datetime.now(timezone.utc),
        description="Re-simulate test outage",
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

    dispute_id = db.execute(
        insert(SLADispute.__table__).values(
            sla_result_id=sla_result.id,
            baseline_sla_result_id=sla_result.id,
            sla_id=sla_result.id,  # legacy NOT NULL column on the merged table
            reason="MTTR exceeded due to third-party dependency outage",  # legacy NOT NULL column
            flagged_by="ops-operator",
            dispute_reason="MTTR exceeded due to third-party dependency outage",
        )
    ).inserted_primary_key[0]
    db.commit()
    return dispute_id, outage_id


def test_re_simulate_returns_original_vs_simulated(client: TestClient, db: Session):
    dispute_id, outage_id = _seed_dispute(db)

    response = client.post(
        f"/api/v1/disputes/{dispute_id}/re-simulate",
        json={"mttr_minutes": 30},
    )
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["dispute_id"] == str(dispute_id)
    assert data["outage_id"] == outage_id
    assert data["original"]["payment_type"] == "penalty"
    assert data["original"]["amount"] == 500.0
    assert "simulated" in data
    assert data["simulated"]["mttr_minutes"] == 30
    assert "penalty_delta" in data
    assert isinstance(data["penalty_delta"], (int, float))


def test_re_simulate_404_for_missing_dispute(client: TestClient, db: Session):
    response = client.post(
        "/api/v1/disputes/does-not-exist/re-simulate",
        json={"mttr_minutes": 30},
    )
    assert response.status_code == 404


def test_re_simulate_rejects_negative_mttr(client: TestClient, db: Session):
    dispute_id, _ = _seed_dispute(db)
    response = client.post(
        f"/api/v1/disputes/{dispute_id}/re-simulate",
        json={"mttr_minutes": -5},
    )
    assert response.status_code == 422
