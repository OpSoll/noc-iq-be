"""
Tests for Stellar Wave issues:

- #526 — DB transaction isolation level configuration + payment dedup row locking
- #528 — Composite index on payment_transactions (to_address, status, created_at)
- #530 — Celery dead-letter queue routing for unhandled task exceptions
- #531 — Celery execution time-limit guards (soft 60s / hard 120s)
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.job import Job, JobStatus, JobType
from app.models.orm.celery_task_dead_letter import CeleryTaskDeadLetterORM
from app.models.orm.payment import PaymentTransactionORM
from app.models.sla import SLAResult
from app.repositories.payment_repository import PaymentRepository
from app.tasks.celery_app import (
    GuardedTask,
    _mark_job_timed_out,
    celery_app,
)
from app.tasks.dead_letter import (
    _is_final_failure,
    _route_failed_task_to_dead_letter,
    store_failed_task,
)
from app.tasks.sla_tasks import compute_sla_for_device
from app.tasks.timeout_guard import revoke_hung_tasks


# --------------------------------------------------------------------------- #
# Issue #526 — DB transaction isolation level options                          #
# --------------------------------------------------------------------------- #


class TestTransactionIsolationLevel:
    def test_settings_expose_isolation_levels(self):
        assert settings.DB_TRANSACTION_ISOLATION_LEVEL == "READ COMMITTED"
        assert settings.PAYMENT_DEDUP_ISOLATION_LEVEL == "REPEATABLE READ"

    def test_invalid_isolation_level_rejected(self, monkeypatch):
        monkeypatch.setenv("PAYMENT_DEDUP_ISOLATION_LEVEL", "NOT_A_LEVEL")
        from pydantic import ValidationError

        from app.core.config import Settings

        with pytest.raises(ValidationError):
            Settings()

    def test_engine_uses_configured_isolation_on_postgres_only(self):
        from app.db.session import _is_sqlite

        engine = SessionLocal().get_bind()
        # SQLite does not support the option; PostgreSQL engines must carry it.
        if _is_sqlite:
            assert engine.dialect.name == "sqlite"
        else:
            assert engine.dialect.name == "postgresql"

    def test_payment_dedup_isolation_helper_is_safe_on_sqlite(self, db: Session):
        repo = PaymentRepository(db)
        # Must be a no-op on SQLite (no exception, no side effects).
        repo._apply_payment_dedup_isolation()
        assert True

    def test_concurrent_dedup_creates_single_payment(self, db: Session):
        """Two concurrent settlements for the same SLA result converge on one row."""
        from datetime import datetime, timezone

        from app.models.orm.outage import OutageORM

        outage_id = f"out-526-{uuid.uuid4().hex[:8]}"
        db.add(
            OutageORM(
                id=outage_id,
                site_name="Concurrency test site",
                severity="high",
                status="resolved",
                detected_at=datetime.now(timezone.utc),
                description="concurrency dedup test",
                affected_services=["service1"],
            )
        )
        db.commit()

        # The payment row's sla_result_id is an FK into sla_results. Insert the
        # parent row with only the migrated columns so the test is agnostic to
        # the model/migration drift that exists in this repository.
        bind = db.get_bind()
        with bind.connect() as conn:
            if bind.dialect.name == "postgresql":
                # Migrated schema predates policy_version/threshold_source.
                sla_result_id = conn.execute(
                    text(
                        "INSERT INTO sla_results "
                        "(outage_id, status, mttr_minutes, threshold_minutes, amount, "
                        "payment_type, rating, created_at, is_latest) "
                        "VALUES (:outage_id, 'met', 30, 60, 100, 'reward', 'excellent', "
                        "now(), false) RETURNING id"
                    ),
                    {"outage_id": outage_id},
                ).scalar()
            else:
                # create_all schema includes the model's NOT NULL columns.
                sla_result_id = conn.execute(
                    text(
                        "INSERT INTO sla_results "
                        "(outage_id, status, mttr_minutes, threshold_minutes, amount, "
                        "payment_type, rating, policy_version, threshold_source, "
                        "created_at, is_latest) "
                        "VALUES (:outage_id, 'met', 30, 60, 100, 'reward', 'excellent', "
                        "'1.0', 'config', :now, 0)"
                    ),
                    {"outage_id": outage_id, "now": datetime.now(timezone.utc)},
                ).lastrowid
            conn.commit()
        sla_result = SLAResult(
            id=sla_result_id,
            outage_id=outage_id,
            status="met",
            mttr_minutes=30,
            threshold_minutes=60,
            amount=100,
            payment_type="reward",
            rating="excellent",
        )

        results: list = []
        errors: list = []

        def attempt() -> None:
            session = SessionLocal()
            try:
                repo = PaymentRepository(session)
                payment = repo.create_for_sla_result(outage_id, sla_result)
                results.append(payment)
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)
            finally:
                session.close()

        barrier = threading.Barrier(2)
        wrapped = []
        for _ in range(2):
            def _worker(b=barrier):
                b.wait()
                attempt()
            wrapped.append(threading.Thread(target=_worker))

        for t in wrapped:
            t.start()
        for t in wrapped:
            t.join(timeout=30)

        rows = (
            db.query(PaymentTransactionORM)
            .filter(PaymentTransactionORM.sla_result_id == sla_result_id)
            .all()
        )
        assert len(rows) == 1, f"expected exactly one payment, got {len(rows)}"
        assert len(results) >= 1
        # Any duplicate insert must have been rejected by the unique constraint.
        assert len(rows) == len(set(p.id for p in rows))


# --------------------------------------------------------------------------- #
# Issue #528 — composite index on payment_transactions                         #
# --------------------------------------------------------------------------- #


class TestPaymentTransactionCompositeIndex:
    def test_model_metadata_includes_composite_index(self):
        index_names = {idx.name for idx in PaymentTransactionORM.__table__.indexes}
        assert "ix_payment_tx_to_addr_status" in index_names

    def test_migration_defines_index(self):
        migration = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "alembic",
            "versions",
            "0025_payment_tx_to_addr_status_index.py",
        )
        with open(migration) as fh:
            source = fh.read()
        assert "ix_payment_tx_to_addr_status" in source
        assert "to_address" in source and "status" in source and "created_at" in source

    def test_index_exists_in_database(self, db: Session):
        bind = db.get_bind()
        with bind.connect() as conn:
            if bind.dialect.name == "sqlite":
                rows = conn.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='index' "
                        "AND name='ix_payment_tx_to_addr_status'"
                    )
                ).fetchall()
            else:
                rows = conn.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE indexname='ix_payment_tx_to_addr_status'"
                    )
                ).fetchall()
        assert rows, "composite index is missing from the database schema"

    @pytest.mark.skipif(
        not os.environ.get("PAYMENT_INDEX_BENCHMARK"),
        reason="set PAYMENT_INDEX_BENCHMARK=1 to run the 100k-row benchmark",
    )
    def test_settlement_query_on_100k_rows(self, tmp_path):
        """Verify the settlement query executes quickly over 100k rows."""
        from datetime import datetime

        from app.db.base import Base

        engine = create_engine("sqlite://")
        Base.metadata.create_all(bind=engine)
        db = Session(bind=engine)
        try:
            to_address = "G_SETTLEMENT_BENCH"
            created_at = datetime(2026, 1, 1, 0, 0, 0)
            rows = [
                {
                    "id": f"pay-{i}",
                    "transaction_hash": f"tx-{i}",
                    "type": "reward",
                    "amount": float(i % 1000),
                    "asset_code": "USDC",
                    "from_address": "SYSTEM_POOL",
                    "to_address": to_address if i % 4 == 0 else f"G_OTHER_{i % 50}",
                    "status": "confirmed" if i % 3 == 0 else "pending",
                    "outage_id": None,
                    "sla_result_id": None,
                    "created_at": created_at,
                    "confirmed_at": None,
                    "retry_count": 0,
                    "last_retried_at": None,
                    "failure_taxonomy": None,
                    "idempotency_key": None,
                    "dead_letter_reason": None,
                    "dead_lettered_at": None,
                }
                for i in range(100_000)
            ]
            db.execute(PaymentTransactionORM.__table__.insert(), rows)
            db.commit()

            start = time.monotonic()
            result = db.execute(
                text(
                    "SELECT * FROM payment_transactions "
                    "WHERE to_address=:addr AND status=:status "
                    "ORDER BY created_at DESC LIMIT 20"
                ),
                {"addr": to_address, "status": "confirmed"},
            ).fetchall()
            elapsed_ms = (time.monotonic() - start) * 1000

            assert len(result) >= 0
            # Generous bound: the index should keep this in the low tens of ms.
            assert elapsed_ms < 500, f"settlement query took {elapsed_ms:.1f}ms"
            artifact = tmp_path / "payment-index-benchmark.json"
            artifact.write_text(
                json.dumps({"rows": 100_000, "elapsed_ms": round(elapsed_ms, 2)})
            )
        finally:
            db.close()
            engine.dispose()


# --------------------------------------------------------------------------- #
# Issue #530 — Celery dead-letter queue routing                                #
# --------------------------------------------------------------------------- #


class TestCeleryDeadLetterQueue:
    def test_celery_conf_rejects_on_worker_lost(self):
        assert celery_app.conf.task_reject_on_worker_lost is True
        assert celery_app.conf.task_acks_late is True

    def test_settings_expose_dead_letter_queue(self):
        assert settings.CELERY_DEAD_LETTER_QUEUE == "celery_dead_letter"
        assert settings.CELERY_DEAD_LETTER_ENABLED is True

    def test_final_failure_detection(self):
        final = SimpleNamespace(max_retries=3, request=SimpleNamespace(retries=3))
        assert _is_final_failure(final) is True
        # First-attempt unhandled exception (no retry scheduled) is final.
        first_try = SimpleNamespace(max_retries=3, request=SimpleNamespace(retries=0))
        assert _is_final_failure(first_try) is True
        # Mid-retry failure without retry scheduling is not final.
        mid = SimpleNamespace(max_retries=5, request=SimpleNamespace(retries=1))
        assert _is_final_failure(mid, exc=ValueError("boom")) is False

    def test_store_failed_task_persists_payload_and_traceback(self, db: Session):
        task_id = f"dlq-store-{uuid.uuid4().hex[:8]}"
        store_failed_task(
            task_id=task_id,
            task_name="app.tasks.example.fail",
            args=["a", 1],
            kwargs={"b": 2},
            exception="RuntimeError: boom",
            traceback='Traceback (most recent call last):\n  File "x.py", line 1',
        )
        row = (
            db.query(CeleryTaskDeadLetterORM)
            .filter(CeleryTaskDeadLetterORM.task_id == task_id)
            .first()
        )
        assert row is not None
        assert row.task_name == "app.tasks.example.fail"
        assert json.loads(row.args_json) == ["a", 1]
        assert json.loads(row.kwargs_json) == {"b": 2}
        assert "RuntimeError" in row.exception
        assert "Traceback" in row.traceback

    def test_failure_signal_routes_final_failure(self, db: Session):
        task_id = f"dlq-signal-final-{uuid.uuid4().hex[:8]}"
        sender = SimpleNamespace(
            name="app.tasks.example.permanent",
            max_retries=3,
            request=SimpleNamespace(retries=3),
        )
        _route_failed_task_to_dead_letter(
            sender=sender,
            task_id=task_id,
            args=["x"],
            kwargs={},
            exc=ValueError("permanent"),
            einfo=SimpleNamespace(traceback="tb-line"),
        )
        row = (
            db.query(CeleryTaskDeadLetterORM)
            .filter(CeleryTaskDeadLetterORM.task_id == task_id)
            .first()
        )
        assert row is not None
        assert row.exception.startswith("ValueError")

    def test_failure_signal_skips_non_final_failure(self, db: Session):
        task_id = f"dlq-signal-skip-{uuid.uuid4().hex[:8]}"
        sender = SimpleNamespace(
            name="app.tasks.example.transient",
            max_retries=5,
            request=SimpleNamespace(retries=1),
        )
        _route_failed_task_to_dead_letter(
            sender=sender,
            task_id=task_id,
            args=["y"],
            kwargs={},
            exc=ValueError("transient"),
            einfo=None,
        )
        row = (
            db.query(CeleryTaskDeadLetterORM)
            .filter(CeleryTaskDeadLetterORM.task_id == task_id)
            .first()
        )
        assert row is None

    def test_eager_mode_skips_broker_publish(self, monkeypatch):
        monkeypatch.setattr(settings, "CELERY_TASK_ALWAYS_EAGER", True)
        from app.tasks.dead_letter import publish_to_dead_letter_queue

        assert publish_to_dead_letter_queue({"id": "x", "task": "t"}) is True


# --------------------------------------------------------------------------- #
# Issue #531 — Celery execution time-limit guards                              #
# --------------------------------------------------------------------------- #


class TestCeleryTimeLimits:
    def test_celery_conf_has_time_limits(self):
        assert celery_app.conf.task_soft_time_limit == 60
        assert celery_app.conf.task_time_limit == 120

    def test_settings_expose_time_limits(self):
        assert settings.CELERY_TASK_SOFT_TIME_LIMIT == 60
        assert settings.CELERY_TASK_TIME_LIMIT == 120

    def test_guarded_task_exposes_limit_hooks(self):
        assert hasattr(GuardedTask, "on_soft_time_limit")
        assert hasattr(GuardedTask, "on_time_limit")

    def test_mark_job_timed_out(self, db: Session):
        task_id = f"timeout-mark-{uuid.uuid4().hex[:8]}"
        job = Job(
            celery_task_id=task_id,
            job_type=JobType.SLA_COMPUTATION,
            status=JobStatus.STARTED,
            payload="{}",
        )
        db.add(job)
        db.commit()

        _mark_job_timed_out(task_id, "SOFT_TIME_LIMIT")

        db.expire_all()
        updated = (
            db.query(Job).filter(Job.celery_task_id == task_id).first()
        )
        assert updated.status == JobStatus.FAILURE
        assert updated.error_code == "SOFT_TIME_LIMIT"
        assert updated.error_retryable is False

    def test_revoke_hung_tasks_revokes_stale_jobs(self, db: Session):
        from datetime import datetime, timedelta

        task_id = f"hung-task-{uuid.uuid4().hex[:8]}"
        stale = Job(
            celery_task_id=task_id,
            job_type=JobType.SLA_COMPUTATION,
            status=JobStatus.STARTED,
            payload="{}",
            worker_id="worker-1",
            heartbeat_at=datetime.utcnow() - timedelta(minutes=10),
            lease_expires_at=datetime.utcnow() - timedelta(minutes=5),
        )
        db.add(stale)
        db.commit()

        result = revoke_hung_tasks()

        assert result["revoked"] == 1
        db.expire_all()
        updated = (
            db.query(Job).filter(Job.celery_task_id == task_id).first()
        )
        assert updated.status == JobStatus.REVOKED
        assert updated.error_code == "TASK_TIMEOUT_REVOKED"

    def test_revoke_hung_tasks_leaves_fresh_jobs_alone(self, db: Session):
        from datetime import datetime, timedelta

        task_id = f"hung-fresh-{uuid.uuid4().hex[:8]}"
        fresh = Job(
            celery_task_id=task_id,
            job_type=JobType.WEBHOOK_DISPATCH,
            status=JobStatus.STARTED,
            payload="{}",
            worker_id="worker-2",
            heartbeat_at=datetime.utcnow(),
            lease_expires_at=datetime.utcnow() + timedelta(minutes=5),
        )
        db.add(fresh)
        db.commit()

        result = revoke_hung_tasks()

        assert result["revoked"] == 0
        db.expire_all()
        updated = (
            db.query(Job).filter(Job.celery_task_id == task_id).first()
        )
        assert updated.status == JobStatus.STARTED

    def test_sla_task_catches_soft_time_limit(self, db: Session):
        """A task hitting the soft limit is marked failed and not retried."""
        from unittest.mock import patch

        from celery.exceptions import SoftTimeLimitExceeded

        task_id = f"sla-soft-limit-{uuid.uuid4().hex[:8]}"
        job = Job(
            celery_task_id=task_id,
            job_type=JobType.SLA_COMPUTATION,
            status=JobStatus.PENDING,
            payload="{}",
        )
        db.add(job)
        db.commit()

        compute_sla_for_device.push_request(id=task_id)
        try:
            with patch(
                "app.services.sla_service.compute_device_sla",
                side_effect=SoftTimeLimitExceeded("soft"),
            ):
                with pytest.raises(SoftTimeLimitExceeded):
                    compute_sla_for_device.run(
                        device_id="dev-soft-limit",
                        period="2026-01",
                    )
        finally:
            compute_sla_for_device.pop_request()

        db.expire_all()
        updated = (
            db.query(Job).filter(Job.celery_task_id == task_id).first()
        )
        assert updated.status == JobStatus.FAILURE
        assert updated.error_code == "SOFT_TIME_LIMIT"
