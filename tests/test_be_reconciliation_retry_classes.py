"""Tests for reconciliation, retry classes, and failure taxonomy.

Covers:
- RetryClass enum and classify_error function
- ReconciliationCategory enum and ReconciliationReport model
- failure_taxonomy field on PaymentTransaction
- reconcile_all repository method
- create_with_submit repository method
"""
import pytest
from datetime import datetime
from unittest.mock import MagicMock

from app.models.payment import (
    PaymentTransaction,
    ReconciliationCategory,
    ReconciliationReport,
    RetryClass,
)
from app.models.orm.payment import PaymentTransactionORM
from app.repositories.payment_repository import PaymentRepository
from app.services.contracts.sla_adapter import check_blockchain_payment_status, classify_error


class TestRetryClass:
    def test_retry_class_values(self):
        assert RetryClass.network.value == "network"
        assert RetryClass.rate_limit.value == "rate_limit"
        assert RetryClass.semantic.value == "semantic"
        assert RetryClass.unknown.value == "unknown"

    def test_retry_class_from_string(self):
        assert RetryClass("network") == RetryClass.network
        assert RetryClass("rate_limit") == RetryClass.rate_limit


class TestClassifyError:
    def test_timeout_classified_as_network(self):
        assert classify_error(TimeoutError("connection timed out")) == RetryClass.network

    def test_rate_limit_classified_as_rate_limit(self):
        assert classify_error(Exception("Rate limit exceeded: 429")) == RetryClass.rate_limit

    def test_invalid_classified_as_semantic(self):
        assert classify_error(ValueError("invalid request body")) == RetryClass.semantic

    def test_unknown_error_classified_as_unknown(self):
        assert classify_error(Exception("some random error")) == RetryClass.unknown


class TestReconciliationCategory:
    def test_reconciliation_category_values(self):
        assert ReconciliationCategory.matched.value == "matched"
        assert ReconciliationCategory.delayed.value == "delayed"
        assert ReconciliationCategory.missing.value == "missing"
        assert ReconciliationCategory.divergent.value == "divergent"


class TestReconciliationReport:
    def test_matched_report(self):
        report = ReconciliationReport(
            transaction_id="pay-1",
            local_status="confirmed",
            blockchain_status="confirmed",
            category=ReconciliationCategory.matched,
        )
        assert report.transaction_id == "pay-1"
        assert report.category == ReconciliationCategory.matched
        assert report.details is None

    def test_missing_report(self):
        report = ReconciliationReport(
            transaction_id="pay-2",
            local_status="unknown",
            blockchain_status="confirmed",
            category=ReconciliationCategory.missing,
            details={"reason": "not found"},
        )
        assert report.category == ReconciliationCategory.missing
        assert report.details["reason"] == "not found"


class TestFailureTaxonomyField:
    def test_failure_taxonomy_default_none(self):
        tx = PaymentTransaction(
            id="pay-1", transaction_hash="tx-1", type="reward", amount=100.0,
            asset_code="USDC", from_address="SYSTEM", to_address="USER",
            status="failed", outage_id="out-1", created_at=datetime(2026, 1, 1),
        )
        assert tx.failure_taxonomy is None

    def test_failure_taxonomy_set(self):
        tx = PaymentTransaction(
            id="pay-1", transaction_hash="tx-1", type="reward", amount=100.0,
            asset_code="USDC", from_address="SYSTEM", to_address="USER",
            status="failed", outage_id="out-1", created_at=datetime(2026, 1, 1),
            failure_taxonomy="network",
        )
        assert tx.failure_taxonomy == "network"


class TestFailureTaxonomyORM:
    def test_failure_taxonomy_column_exists(self):
        orm = PaymentTransactionORM.__table__
        assert "failure_taxonomy" in orm.c

    def test_failure_taxonomy_column_nullable(self):
        col = PaymentTransactionORM.__table__.c["failure_taxonomy"]
        assert col.nullable is True


class TestReconcileAll:
    def test_reconcile_all_returns_matched(self):
        db = MagicMock()
        orm = MagicMock()
        orm.id = "pay-1"
        orm.transaction_hash = "tx-1"
        orm.type = "reward"
        orm.amount = 100.0
        orm.asset_code = "USDC"
        orm.from_address = "SYSTEM"
        orm.to_address = "USER"
        orm.status = "confirmed"
        orm.outage_id = "out-1"
        orm.sla_result_id = None
        orm.created_at = datetime(2026, 1, 1)
        orm.confirmed_at = datetime(2026, 1, 1)
        orm.retry_count = 0
        orm.last_retried_at = None
        orm.failure_taxonomy = None
        db.query.return_value.filter.return_value.first.return_value = orm

        repo = PaymentRepository(db)
        reports = repo.reconcile_all({"pay-1": "confirmed"})
        assert len(reports) == 1
        assert reports[0].category == ReconciliationCategory.matched

    def test_reconcile_all_returns_missing(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        repo = PaymentRepository(db)
        reports = repo.reconcile_all({"pay-1": "confirmed"})
        assert len(reports) == 1
        assert reports[0].category == ReconciliationCategory.missing

    def test_reconcile_all_returns_delayed(self):
        db = MagicMock()
        orm = MagicMock()
        orm.status = "pending"
        orm.id = "pay-1"
        orm.transaction_hash = "tx-1"
        orm.type = "reward"
        orm.amount = 100.0
        orm.asset_code = "USDC"
        orm.from_address = "SYSTEM"
        orm.to_address = "USER"
        orm.outage_id = "out-1"
        orm.sla_result_id = None
        orm.created_at = datetime(2026, 1, 1)
        orm.confirmed_at = None
        orm.retry_count = 0
        orm.last_retried_at = None
        orm.failure_taxonomy = None
        db.query.return_value.filter.return_value.first.return_value = orm

        repo = PaymentRepository(db)
        reports = repo.reconcile_all({"pay-1": "confirmed"})
        assert len(reports) == 1
        assert reports[0].category == ReconciliationCategory.delayed

    def test_reconcile_all_returns_divergent(self):
        db = MagicMock()
        orm = MagicMock()
        orm.status = "failed"
        orm.id = "pay-1"
        orm.transaction_hash = "tx-1"
        orm.type = "reward"
        orm.amount = 100.0
        orm.asset_code = "USDC"
        orm.from_address = "SYSTEM"
        orm.to_address = "USER"
        orm.outage_id = "out-1"
        orm.sla_result_id = None
        orm.created_at = datetime(2026, 1, 1)
        orm.confirmed_at = None
        orm.retry_count = 0
        orm.last_retried_at = None
        orm.failure_taxonomy = None
        db.query.return_value.filter.return_value.first.return_value = orm

        repo = PaymentRepository(db)
        reports = repo.reconcile_all({"pay-1": "confirmed"})
        assert len(reports) == 1
        assert reports[0].category == ReconciliationCategory.divergent


class TestCreateWithSubmit:
    def test_create_with_submit_flushes(self):
        db = MagicMock()
        db.add.return_value = None
        db.flush.return_value = None

        tx = PaymentTransaction(
            id="pay-1", transaction_hash="tx-1", type="reward", amount=100.0,
            asset_code="USDC", from_address="SYSTEM", to_address="USER",
            status="pending", outage_id="out-1", created_at=datetime(2026, 1, 1),
        )

        repo = PaymentRepository(db)
        repo.create_with_submit(tx)
        assert db.add.called
        assert db.flush.called
