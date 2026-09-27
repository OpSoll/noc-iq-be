"""
Tests for Stellar Wave issues:

- #516 — Alembic single-head enforcement script
- #519 — GIN index on webhook_deliveries (payload jsonb)
- #522 — Monthly partitioning of webhook_deliveries
- #524 — SQLAlchemy validation of non-negative MTTR values
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text

from app.models.orm.outage import OutageORM

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATABASE_URL = os.environ.get("DATABASE_URL", "")


def _has_postgres() -> bool:
    return "postgres" in _DATABASE_URL


# --------------------------------------------------------------------------- #
# Issue #516 — Alembic single head enforcement                                 #
# --------------------------------------------------------------------------- #


class TestAlembicSingleHead:
    def test_check_script_accepts_single_head(self):
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(_REPO_ROOT, "scripts", "check_alembic_single_head.py"),
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_check_script_rejects_multiple_heads(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "check_alembic_single_head",
            os.path.join(_REPO_ROOT, "scripts", "check_alembic_single_head.py"),
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        fake_run = MagicMock(
            return_value=SimpleNamespace(
                returncode=0,
                stdout="0026_celery_task_dead_letters (head)\n0025_payment_tx_to_addr_status_index (head)\n",
                stderr="",
            )
        )
        with patch.object(module.subprocess, "run", fake_run):
            assert module.main() == 1

    def test_check_script_fails_when_alembic_errors(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "check_alembic_single_head",
            os.path.join(_REPO_ROOT, "scripts", "check_alembic_single_head.py"),
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        fake_run = MagicMock(return_value=SimpleNamespace(returncode=1, stdout="", stderr="boom"))
        with patch.object(module.subprocess, "run", fake_run):
            assert module.main() == 1


# --------------------------------------------------------------------------- #
# Issue #519 — GIN index on webhook_deliveries payload                         #
# --------------------------------------------------------------------------- #


class TestWebhookPayloadGinIndex:
    def test_migration_converts_payload_to_jsonb_with_gin(self):
        migration = os.path.join(
            _REPO_ROOT,
            "alembic",
            "versions",
            "0027_webhook_deliveries_payload_gin.py",
        )
        with open(migration) as fh:
            source = fh.read()
        assert "ix_webhook_deliveries_payload_gin" in source
        assert "TYPE JSONB" in source
        assert "USING GIN (payload)" in source

    @pytest.mark.skipif(not _has_postgres(), reason="requires PostgreSQL")
    def test_payload_column_is_jsonb_with_gin_index(self, db):
        bind = db.get_bind()
        with bind.connect() as conn:
            col_type = conn.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name='webhook_deliveries' AND column_name='payload'"
                )
            ).scalar()
            index = conn.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename='webhook_deliveries' "
                    "AND indexname='ix_webhook_deliveries_payload_gin'"
                )
            ).fetchall()
        assert col_type == "jsonb"
        assert index

    @pytest.mark.skipif(
        not os.environ.get("PAYLOAD_GIN_BENCHMARK") or not _has_postgres(),
        reason="set PAYLOAD_GIN_BENCHMARK=1 on PostgreSQL to run the benchmark",
    )
    def test_payload_containment_query_benchmark(self):
        """Time JSON containment queries over synthetic webhook deliveries."""
        import time

        from sqlalchemy import create_engine

        from app.db.base import Base

        engine = create_engine(_DATABASE_URL)
        Base.metadata.create_all(bind=engine)
        db = engine.connect()
        try:
            webhook_id = str(uuid.uuid4())
            db.execute(
                text(
                    "INSERT INTO webhooks "
                    "(id, name, url, is_active, events, max_retries, created_at, "
                    "updated_at, secret_version) "
                    "VALUES (:id, 'bench', 'https://example.com/bench', true, "
                    "'[\"sla.violation\"]', 3, now(), now(), 1)"
                ),
                {"id": webhook_id},
            )
            db.commit()

            now = datetime.now(timezone.utc)
            rows = []
            for i in range(10_000):
                rows.append(
                    {
                        "id": str(uuid.uuid4()),
                        "webhook_id": webhook_id,
                        "event": "sla.violation",
                        "payload": f'{{"device_id": "dev-{i % 100}", "outage_id": "out-{i % 50}"}}',
                        "status": "pending",
                        "attempt_count": 0,
                        "signature_version": 1,
                        "idempotency_key": f"key-{i}",
                        "event_timestamp": now,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            db.execute(
                text(
                    "INSERT INTO webhook_deliveries "
                    "(id, webhook_id, event, payload, status, attempt_count, "
                    "signature_version, idempotency_key, event_timestamp, "
                    "created_at, updated_at) "
                    "VALUES (:id, :webhook_id, :event, CAST(:payload AS JSONB), "
                    ":status, :attempt_count, :signature_version, "
                    ":idempotency_key, :event_timestamp, :created_at, :updated_at)"
                ),
                rows,
            )
            db.commit()

            start = time.monotonic()
            result = db.execute(
                text(
                    "SELECT count(*) FROM webhook_deliveries "
                    "WHERE payload @> CAST(:needle AS JSONB)"
                ),
                {"needle": '{"device_id": "dev-42"}'},
            ).scalar()
            elapsed_ms = (time.monotonic() - start) * 1000
            assert result > 0
            assert elapsed_ms < 2000, f"containment query took {elapsed_ms:.1f}ms"
        finally:
            db.close()
            engine.dispose()


# --------------------------------------------------------------------------- #
# Issue #522 — Monthly partitioning of webhook_deliveries                      #
# --------------------------------------------------------------------------- #


class TestWebhookDeliveriesPartitioning:
    def test_partition_migration_defines_range_partitioning(self):
        migration = os.path.join(
            _REPO_ROOT,
            "alembic",
            "versions",
            "0028_webhook_deliveries_partitioning.py",
        )
        with open(migration) as fh:
            source = fh.read()
        assert "PARTITION BY RANGE (created_at)" in source
        assert "PARTITION OF webhook_deliveries" in source
        assert "webhook_deliveries_default" in source
        assert "scripts/create_next_partition.py" in source

    def test_next_month_range_computation(self):
        from scripts.create_next_partition import (
            compute_next_month_range,
            partition_name,
        )

        start, end = compute_next_month_range(datetime(2026, 8, 15))
        assert start == datetime(2026, 9, 1)
        assert end == datetime(2026, 10, 1)
        assert partition_name(start) == "webhook_deliveries_2026_09"

    def test_create_next_partition_issues_partition_ddl(self):
        from scripts.create_next_partition import create_next_month_partition

        conn = MagicMock()
        engine = MagicMock()
        engine.begin.return_value.__enter__ = MagicMock(return_value=conn)
        engine.begin.return_value.__exit__ = MagicMock(return_value=False)

        name = create_next_month_partition(engine, now=datetime(2026, 8, 15))

        assert name == "webhook_deliveries_2026_09"
        conn.execute.assert_called_once()
        call = conn.execute.call_args
        sql = call[0][0].text
        assert "PARTITION OF webhook_deliveries" in sql
        assert call[0][1]["start"] == datetime(2026, 9, 1)
        assert call[0][1]["end"] == datetime(2026, 10, 1)

    @pytest.mark.skipif(not _has_postgres(), reason="requires PostgreSQL")
    def test_partition_table_exists_and_parent_is_partitioned(self, db):
        from scripts.create_next_partition import create_next_month_partition

        bind = db.get_bind()
        with bind.connect() as conn:
            relkind = conn.execute(
                text(
                    "SELECT relkind FROM pg_class WHERE relname='webhook_deliveries'"
                )
            ).scalar()
            assert relkind == "p", "webhook_deliveries is not a partitioned table"

        # Creating the next month's partition should be a no-op on the second call.
        name = create_next_month_partition(bind, now=datetime.utcnow())
        with bind.connect() as conn:
            exists = conn.execute(
                text(
                    "SELECT 1 FROM pg_class WHERE relname=:name AND relkind='r'"
                ),
                {"name": name},
            ).fetchall()
        assert exists


# --------------------------------------------------------------------------- #
# Issue #524 — Non-negative MTTR validation                                    #
# --------------------------------------------------------------------------- #


class TestMttrValidation:
    def test_negative_mttr_rejected(self):
        with pytest.raises(ValueError, match="mttr_minutes"):
            OutageORM(
                id="out-524-neg",
                site_name="Site A",
                severity="high",
                status="resolved",
                detected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                description="bad mttr",
                affected_services=["s1"],
                mttr_minutes=-5,
            )

    def test_negative_mttr_rejected_on_assignment(self):
        outage = OutageORM(
            id="out-524-assign",
            site_name="Site B",
            severity="high",
            status="open",
            detected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            description="assign test",
            affected_services=["s1"],
        )
        with pytest.raises(ValueError, match="mttr_minutes"):
            outage.mttr_minutes = -1

    def test_zero_and_positive_mttr_accepted(self):
        outage = OutageORM(
            id="out-524-ok",
            site_name="Site C",
            severity="high",
            status="resolved",
            detected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            description="ok mttr",
            affected_services=["s1"],
            mttr_minutes=0,
        )
        assert outage.mttr_minutes == 0
        outage.mttr_minutes = 120
        assert outage.mttr_minutes == 120

    def test_none_mttr_accepted(self):
        outage = OutageORM(
            id="out-524-none",
            site_name="Site D",
            severity="high",
            status="open",
            detected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            description="none mttr",
            affected_services=["s1"],
        )
        assert outage.mttr_minutes is None
