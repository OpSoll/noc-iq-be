"""Tests for outage soft-delete support (Issue #521).

Verifies:
- ``is_deleted`` / ``deleted_at`` columns exist on the outages model
- repository queries exclude soft-deleted records by default
- ``restore()`` brings a soft-deleted record back into queries
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.orm.outage import OutageORM
from app.repositories.outage_repository import OutageRepository


def _make_outage(db: Session) -> OutageORM:
    outage = OutageORM(
        id=f"out-soft-{uuid.uuid4().hex[:12]}",
        site_name="Site A",
        site_id="site_1",
        severity="high",
        status="open",
        detected_at=datetime.now(timezone.utc),
        description="Soft delete test outage",
        affected_services=["4G"],
    )
    db.add(outage)
    db.commit()
    db.refresh(outage)
    return outage


def test_soft_delete_columns_exist(db: Session):
    outage = _make_outage(db)
    assert outage.is_deleted is False
    assert outage.deleted_at is None


def test_soft_delete_hides_record_from_queries(db: Session):
    outage = _make_outage(db)
    repo = OutageRepository(db)

    result = repo.soft_delete(outage.id)
    assert result is not None
    assert result.deleted_at is not None

    # Default queries must exclude the soft-deleted record.
    assert repo.get(outage.id) is None
    assert repo.get_orm(outage.id) is None
    assert all(o.id != outage.id for o in repo.list_all())
    assert all(o.id != outage.id for o in repo.list()["items"])
    assert all(o.id != outage.id for o in repo.list_filtered())
    assert all(o.id != outage.id for o in repo.list_violations())


def test_soft_delete_keeps_row_for_restore(db: Session):
    outage = _make_outage(db)
    repo = OutageRepository(db)

    repo.soft_delete(outage.id)
    deleted = [d for d in repo.list_deleted() if d.id == outage.id]
    assert len(deleted) == 1
    assert deleted[0].id == outage.id
    assert deleted[0].deleted_at is not None


def test_restore_brings_record_back(db: Session):
    outage = _make_outage(db)
    repo = OutageRepository(db)

    repo.soft_delete(outage.id)
    assert repo.get(outage.id) is None

    restored = repo.restore(outage.id)
    assert restored is not None
    assert restored.deleted_at is None

    found = repo.get(outage.id)
    assert found is not None
    assert found.id == outage.id


def test_restore_missing_record_returns_none(db: Session):
    repo = OutageRepository(db)
    assert repo.restore("out-does-not-exist") is None
    assert repo.soft_delete("out-does-not-exist") is None
