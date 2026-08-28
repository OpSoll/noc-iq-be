"""Tests for foreign key ON DELETE CASCADE rules on dispute child tables (Issue #523).

Verifies that deleting a parent outage record cascades through
``sla_results`` -> ``sla_disputes`` -> ``dispute_audit_logs`` so child
dispute audit logs do not block the deletion.

The runtime cascade test requires PostgreSQL because the ``sla_disputes``
table is mapped by both the legacy and the current ORM model, and the merged
in-memory (SQLite) schema cannot resolve the ``dispute_audit_logs`` foreign
key target. Migrations run against PostgreSQL in CI.
"""
import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.orm.outage import OutageORM
from app.models.orm.sla import SLAResultORM
from app.models.sla_dispute import DisputeAuditLog, SLADispute

requires_postgres = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL")
    or "sqlite" in os.environ["DATABASE_URL"].lower(),
    reason="Runtime cascade verification requires PostgreSQL (DATABASE_URL).",
)


def test_models_use_ondelete_cascade(db: Session):
    from sqlalchemy import inspect

    insp = inspect(db.bind)
    sla_fk = next(
        fk for fk in insp.get_foreign_keys("sla_disputes")
        if fk["constrained_columns"] == ["sla_result_id"]
    )
    assert sla_fk["options"].get("ondelete", "").upper() == "CASCADE"

    audit_fk = next(
        fk for fk in insp.get_foreign_keys("dispute_audit_logs")
        if fk["constrained_columns"] == ["dispute_id"]
    )
    assert audit_fk["options"].get("ondelete", "").upper() == "CASCADE"


@requires_postgres
def test_cascade_delete_removes_dispute_audit_logs(db: Session):
    outage = OutageORM(
        id=f"out-cascade-{uuid.uuid4().hex[:10]}",
        site_name="Site A",
        site_id="site_1",
        severity="high",
        status="resolved",
        detected_at=datetime.now(timezone.utc),
        description="Cascade test outage",
        affected_services=["4G"],
    )
    db.add(outage)
    db.flush()

    # Core inserts matching the *migrated* schema (the ORM models contain
    # columns that the migrations never created — the drift this issue family
    # addresses). Only insert columns that exist after `alembic upgrade head`.
    # Raw SQL inserts matching the *migrated* schema exactly (the ORM models
    # declare extra columns the migrations never created — the drift this issue
    # family addresses).
    now = datetime.now(timezone.utc)
    sla_result_id = db.execute(
        text(
            "INSERT INTO sla_results (outage_id, status, mttr_minutes, threshold_minutes, "
            "amount, payment_type, rating, created_at, is_latest) "
            "VALUES (:outage_id, :status, :mttr, :threshold, :amount, :ptype, :rating, :created_at, :latest) "
            "RETURNING sla_results.id"
        ),
        {
            "outage_id": outage.id,
            "status": "violated",
            "mttr": 120,
            "threshold": 60,
            "amount": 250.0,
            "ptype": "penalty",
            "rating": "poor",
            "created_at": now,
            "latest": True,
        },
    ).scalar()

    dispute_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO sla_disputes (id, sla_result_id, flagged_by, dispute_reason, "
            "flagged_at, status) "
            "VALUES (:id, :sla_result_id, :flagged_by, :dispute_reason, :flagged_at, :status)"
        ),
        {
            "id": dispute_id,
            "sla_result_id": sla_result_id,
            "flagged_by": "ops-operator",
            "dispute_reason": "MTTR exceeded due to third-party dependency outage",
            "flagged_at": now,
            "status": "pending",
        },
    )
    audit_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO dispute_audit_logs (id, dispute_id, action, actor, notes, recorded_at) "
            "VALUES (:id, :dispute_id, :action, :actor, :notes, :recorded_at)"
        ),
        {
            "id": audit_id,
            "dispute_id": dispute_id,
            "action": "flagged",
            "actor": "ops-operator",
            "notes": "Dispute flagged for review",
            "recorded_at": now,
        },
    )
    db.commit()

    # Sanity: rows exist before deletion.
    assert db.execute(
        select(func.count()).select_from(SLAResultORM.__table__)
    ).scalar() >= 1

    # Deleting the parent outage must cascade through the child tables.
    db.delete(outage)
    db.commit()

    assert (
        db.execute(
            select(func.count())
            .select_from(SLAResultORM.__table__)
            .where(SLAResultORM.__table__.c.id == sla_result_id)
        ).scalar()
        == 0
    )
    assert (
        db.execute(
            select(func.count())
            .select_from(SLADispute.__table__)
            .where(SLADispute.__table__.c.id == dispute_id)
        ).scalar()
        == 0
    )
    assert (
        db.execute(
            select(func.count())
            .select_from(DisputeAuditLog.__table__)
            .where(DisputeAuditLog.__table__.c.id == audit_id)
        ).scalar()
        == 0
    )
