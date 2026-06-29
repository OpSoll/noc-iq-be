"""Tests for SLA status, SLA simulation, and payment idempotency.

Covers:
- BE-W5-009: SLA status endpoint models
- BE-W5-010: SLA simulation service
- Payment idempotency key and update_status validation
"""
import pytest
from datetime import datetime
from unittest.mock import MagicMock

from app.models.sla import SLAState, SLAStatusResponse
from app.models.payment import PaymentTransaction, PaymentTransitionError, validate_transition
from app.models.orm.payment import PaymentTransactionORM
from app.repositories.payment_repository import PaymentRepository


class TestSLAState:
    def test_sla_state_values(self):
        assert SLAState.in_progress.value == "in_progress"
        assert SLAState.met.value == "met"
        assert SLAState.violated.value == "violated"

    def test_sla_state_from_string(self):
        assert SLAState("in_progress") == SLAState.in_progress
        assert SLAState("met") == SLAState.met
        assert SLAState("violated") == SLAState.violated

    def test_sla_state_invalid_raises(self):
        with pytest.raises(ValueError):
            SLAState("unknown")


class TestSLAStatusResponse:
    def test_minimal_response(self):
        r = SLAStatusResponse(
            outage_id="out-1",
            state=SLAState.in_progress,
            threshold_minutes=60,
        )
        assert r.outage_id == "out-1"
        assert r.state == SLAState.in_progress
        assert r.threshold_minutes == 60
        assert r.mttr_minutes is None
        assert r.time_remaining_minutes is None

    def test_full_response(self):
        r = SLAStatusResponse(
            outage_id="out-1",
            state=SLAState.violated,
            mttr_minutes=90,
            threshold_minutes=60,
            time_remaining_minutes=0,
            period_start="2026-01-01T00:00:00Z",
            period_end="2026-01-02T00:00:00Z",
        )
        assert r.state == SLAState.violated
        assert r.mttr_minutes == 90
        assert r.time_remaining_minutes == 0

    def test_response_serialization(self):
        r = SLAStatusResponse(
            outage_id="out-1",
            state=SLAState.met,
            threshold_minutes=60,
        )
        data = r.model_dump()
        assert data["outage_id"] == "out-1"
        assert data["state"] == "met"
        assert data["threshold_minutes"] == 60


class TestSimulateThresholdChange:
    def test_returns_comparison_structure(self):
        import app.services.sla_service as sla_svc

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        original = sla_svc.compute_device_sla

        def mock_compute(db, device_id, period, thresholds):
            if thresholds and thresholds.get("uptime") == 99.5:
                return {
                    "is_violated": True, "violation_reasons": ["uptime_below_threshold"],
                    "total_outages": 3, "availability_percentage": 97.0, "avg_mttr_minutes": 30.0,
                }
            return {
                "is_violated": False, "violation_reasons": [], "sla_thresholds": {"uptime": 99.0},
            }

        sla_svc.compute_device_sla = mock_compute
        result = sla_svc.simulate_threshold_change(
            db=db, device_id="dev-001", period="2026-01",
            proposed_thresholds={"uptime": 99.5}, sla_thresholds={"uptime": 99.0},
        )
        sla_svc.compute_device_sla = original

        assert result["device_id"] == "dev-001"
        assert result["current"]["is_violated"] is False
        assert result["simulated"]["is_violated"] is True
        assert result["projected_outages"] == 3

    def test_handles_no_current_thresholds(self):
        import app.services.sla_service as sla_svc

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        original = sla_svc.compute_device_sla

        def mock_compute(db, device_id, period, thresholds):
            return {
                "is_violated": False, "violation_reasons": [],
                "total_outages": 0, "availability_percentage": 100.0, "avg_mttr_minutes": 0.0,
            }

        sla_svc.compute_device_sla = mock_compute
        result = sla_svc.simulate_threshold_change(
            db=db, device_id="dev-002", period="2026-02",
            proposed_thresholds={"uptime": 99.9},
        )
        sla_svc.compute_device_sla = original

        assert result["device_id"] == "dev-002"
        assert "current" in result
        assert "simulated" in result


class TestPaymentIdempotencyKey:
    def test_in_pydantic_model(self):
        tx = PaymentTransaction(
            id="pay-1", transaction_hash="tx-1", type="reward", amount=100.0,
            asset_code="USDC", from_address="SYSTEM", to_address="USER",
            status="pending", outage_id="out-1", created_at=datetime(2026, 1, 1),
            idempotency_key="sla_result_42_reward",
        )
        assert tx.idempotency_key == "sla_result_42_reward"

    def test_default_none(self):
        tx = PaymentTransaction(
            id="pay-2", transaction_hash="tx-2", type="penalty", amount=50.0,
            asset_code="USDC", from_address="SYSTEM", to_address="USER",
            status="pending", outage_id="out-1", created_at=datetime(2026, 1, 1),
        )
        assert tx.idempotency_key is None

    def test_serialization(self):
        tx = PaymentTransaction(
            id="pay-3", transaction_hash="tx-3", type="reward", amount=100.0,
            asset_code="USDC", from_address="SYSTEM", to_address="USER",
            status="pending", outage_id="out-1", created_at=datetime(2026, 1, 1),
            idempotency_key="sla_result_99_penalty",
        )
        data = tx.model_dump()
        assert data["idempotency_key"] == "sla_result_99_penalty"


class TestPaymentORMIdempotencyKey:
    def test_column_exists(self):
        orm = PaymentTransactionORM.__table__
        assert "idempotency_key" in orm.c

    def test_column_nullable(self):
        col = PaymentTransactionORM.__table__.c["idempotency_key"]
        assert col.nullable is True


class TestUpdateStatusValidation:
    def test_valid_transition_pending_to_confirmed(self):
        db = MagicMock()
        orm = MagicMock()
        orm.id = "pay-1"
        orm.transaction_hash = "tx-1"
        orm.type = "reward"
        orm.amount = 100.0
        orm.asset_code = "USDC"
        orm.from_address = "SYSTEM"
        orm.to_address = "USER"
        orm.status = "pending"
        orm.outage_id = "out-1"
        orm.sla_result_id = 1
        orm.created_at = datetime(2026, 1, 1)
        orm.confirmed_at = None
        orm.retry_count = 0
        orm.last_retried_at = None
        orm.idempotency_key = None
        db.query.return_value.filter.return_value.first.return_value = orm

        repo = PaymentRepository(db)
        result = repo.update_status("pay-1", "confirmed")
        assert result is not None
        assert result.status == "confirmed"

    def test_invalid_transition_confirmed_to_pending_raises(self):
        db = MagicMock()
        orm = MagicMock()
        orm.status = "confirmed"
        db.query.return_value.filter.return_value.first.return_value = orm

        repo = PaymentRepository(db)
        with pytest.raises(PaymentTransitionError):
            repo.update_status("pay-1", "pending")

    def test_returns_none_if_not_found(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        repo = PaymentRepository(db)
        result = repo.update_status("nonexistent", "confirmed")
        assert result is None
