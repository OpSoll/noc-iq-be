"""Tests for dead letter queue and cursor-based payment pagination.

Covers:
- Dead letter payment status and transitions
- Dead letter ORM columns
- list_dead_letter and replay_dead_letter repository methods
- CursorPage model and list_cursor repository method
"""
import pytest
from datetime import datetime
from unittest.mock import MagicMock

from app.models.payment import (
    PaymentStatus,
    PaymentTransaction,
    PaymentTransitionError,
    CursorPage,
    validate_transition,
)
from app.models.orm.payment import PaymentTransactionORM
from app.repositories.payment_repository import PaymentRepository


class TestDeadLetterStatus:
    def test_dead_letter_in_payment_status(self):
        assert PaymentStatus.dead_letter.value == "dead_letter"

    def test_dead_letter_transition_to_pending(self):
        validate_transition("dead_letter", "pending")

    def test_dead_letter_cannot_transition_to_confirmed(self):
        with pytest.raises(PaymentTransitionError):
            validate_transition("dead_letter", "confirmed")


class TestDeadLetterModelFields:
    def test_dead_letter_fields_default_none(self):
        tx = PaymentTransaction(
            id="pay-1", transaction_hash="tx-1", type="reward", amount=100.0,
            asset_code="USDC", from_address="SYSTEM", to_address="USER",
            status="dead_letter", outage_id="out-1", created_at=datetime(2026, 1, 1),
        )
        assert tx.dead_letter_reason is None
        assert tx.dead_lettered_at is None
        assert tx.residual == 0.0

    def test_dead_letter_fields_set(self):
        at = datetime(2026, 6, 1)
        tx = PaymentTransaction(
            id="pay-1", transaction_hash="tx-1", type="reward", amount=100.0,
            asset_code="USDC", from_address="SYSTEM", to_address="USER",
            status="dead_letter", outage_id="out-1", created_at=datetime(2026, 1, 1),
            dead_letter_reason="max_retries_exceeded",
            dead_lettered_at=at,
            residual=12.50,
        )
        assert tx.dead_letter_reason == "max_retries_exceeded"
        assert tx.dead_lettered_at == at
        assert tx.residual == 12.50


class TestDeadLetterORMColumns:
    def test_dead_letter_columns_exist(self):
        orm = PaymentTransactionORM.__table__
        assert "dead_letter_reason" in orm.c
        assert "dead_lettered_at" in orm.c

    def test_dead_letter_columns_nullable(self):
        reason_col = PaymentTransactionORM.__table__.c["dead_letter_reason"]
        assert reason_col.nullable is True
        at_col = PaymentTransactionORM.__table__.c["dead_lettered_at"]
        assert at_col.nullable is True


class TestListDeadLetter:
    def test_list_dead_letter_returns_filtered(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        repo = PaymentRepository(db)
        result = repo.list_dead_letter()
        assert result == []

    def test_list_dead_letter_filters_by_status(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        repo = PaymentRepository(db)
        repo.list_dead_letter()
        assert db.query.return_value.filter.called


class TestReplayDeadLetter:
    def test_replay_dead_letter_resets_status(self):
        db = MagicMock()
        orm = MagicMock()
        orm.status = "dead_letter"
        orm.retry_count = 5
        orm.dead_letter_reason = "max_retries"
        orm.dead_lettered_at = datetime(2026, 6, 1)
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
        orm.dead_letter_reason = None
        orm.dead_lettered_at = None
        db.query.return_value.filter.return_value.first.return_value = orm

        repo = PaymentRepository(db)
        result = repo.replay_dead_letter("pay-1")
        assert result is not None
        assert orm.status == "pending"

    def test_replay_dead_letter_returns_none_if_not_found(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        repo = PaymentRepository(db)
        assert repo.replay_dead_letter("nonexistent") is None

    def test_replay_dead_letter_returns_none_if_not_dead_letter(self):
        db = MagicMock()
        orm = MagicMock()
        orm.status = "confirmed"
        db.query.return_value.filter.return_value.first.return_value = orm
        repo = PaymentRepository(db)
        assert repo.replay_dead_letter("pay-1") is None


class TestCursorPage:
    def test_cursor_page_empty(self):
        page = CursorPage(items=[], next_cursor=None, has_more=False)
        assert page.items == []
        assert page.next_cursor is None
        assert page.has_more is False

    def test_cursor_page_with_items(self):
        tx = PaymentTransaction(
            id="pay-1", transaction_hash="tx-1", type="reward", amount=100.0,
            asset_code="USDC", from_address="SYSTEM", to_address="USER",
            status="pending", outage_id="out-1", created_at=datetime(2026, 1, 1),
        )
        page = CursorPage(items=[tx], next_cursor="2026-01-01T00:00:00,pay-1", has_more=True)
        assert len(page.items) == 1
        assert page.next_cursor == "2026-01-01T00:00:00,pay-1"
        assert page.has_more is True

    def test_cursor_page_serialization(self):
        tx = PaymentTransaction(
            id="pay-1", transaction_hash="tx-1", type="reward", amount=100.0,
            asset_code="USDC", from_address="SYSTEM", to_address="USER",
            status="pending", outage_id="out-1", created_at=datetime(2026, 1, 1),
        )
        page = CursorPage(items=[tx], next_cursor="cursor-val", has_more=True)
        data = page.model_dump(mode="json")
        assert "items" in data
        assert data["next_cursor"] == "cursor-val"
        assert data["has_more"] is True


class TestListCursor:
    def test_list_cursor_no_cursor(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        repo = PaymentRepository(db)
        result = repo.list_cursor(limit=20)
        assert isinstance(result, CursorPage)
        assert result.items == []

    def test_list_cursor_applies_filters(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        repo = PaymentRepository(db)
        repo.list_cursor(status="pending")
        assert db.query.return_value.filter.called
