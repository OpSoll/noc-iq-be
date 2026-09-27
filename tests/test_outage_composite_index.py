"""Tests for the composite index on outages (site_id, status, detected_at) (Issue #515).

Verifies the ORM model declares the index and that the migration
``0025_add_outages_site_status_idx`` creates the exact expected index name.
"""
from sqlalchemy import inspect
from sqlalchemy.orm import Session


def test_orm_model_declares_composite_index(db: Session):
    indexes = {ix["name"]: ix for ix in inspect(db.bind).get_indexes("outages")}
    assert "ix_outages_site_status_detected" in indexes
    assert indexes["ix_outages_site_status_detected"]["column_names"] == [
        "site_id",
        "status",
        "detected_at",
    ]


def test_migration_creates_composite_index():
    import re
    from pathlib import Path

    migration = (
        Path(__file__).parent.parent
        / "alembic"
        / "versions"
        / "0025_add_outages_site_status_idx.py"
    )
    assert migration.exists(), "Migration 0025_add_outages_site_status_idx is missing"
    src = migration.read_text()

    assert "ix_outages_site_status_detected" in src
    assert re.search(r'op\.create_index\(\s*"ix_outages_site_status_detected"', src)
    assert "site_id" in src and "status" in src and "detected_at" in src
    assert "op.drop_index" in src  # downgrade must remove the index
