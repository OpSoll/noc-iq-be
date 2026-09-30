"""Tests for payment transaction timeout bounds.

Validates:
1. TimeBounds creation with default values (min=0, max=now+300)
2. Transaction expiry after 5 minutes
3. Re-queue for fee re-estimation
4. Sweep of stale transactions
"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch, MagicMock


class TestTimeBounds(unittest.TestCase):
    """Test TimeBounds model behavior."""

    def test_default_time_bounds_min_is_zero(self):
        """Default TimeBounds should have min_time=0."""
        from app.models.payment import TimeBounds
        tb = TimeBounds.default_for_transaction()
        self.assertEqual(tb.min_time, 0)

    def test_default_time_bounds_max_is_now_plus_300(self):
        """Default TimeBounds max_time should be now + 300 seconds."""
        from app.models.payment import TimeBounds, TRANSACTION_TIMEOUT_MAX_SECONDS
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        tb = TimeBounds.default_for_transaction(now_utc=now)
        expected_max = int(now.timestamp()) + TRANSACTION_TIMEOUT_MAX_SECONDS
        self.assertEqual(tb.max_time, expected_max)

    def test_time_bounds_is_expired_returns_false_when_within_window(self):
        """is_expired should return False when current time is within bounds."""
        from app.models.payment import TimeBounds
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        tb = TimeBounds(min_time=0, max_time=int(now.timestamp()) + 300)
        # Check at the same time — should not be expired
        self.assertFalse(tb.is_expired(now_utc=now))

    def test_time_bounds_is_expired_returns_true_when_past_max(self):
        """is_expired should return True when current time exceeds max_time."""
        from app.models.payment import TimeBounds
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        tb = TimeBounds(min_time=0, max_time=int(now.timestamp()) - 1)
        self.assertTrue(tb.is_expired(now_utc=now))

    def test_time_bounds_is_expired_exactly_at_max(self):
        """is_expired should return False at exactly max_time."""
        from app.models.payment import TimeBounds
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        tb = TimeBounds(min_time=0, max_time=int(now.timestamp()))
        self.assertFalse(tb.is_expired(now_utc=now))

    def test_custom_time_bounds(self):
        """TimeBounds should accept custom min/max values."""
        from app.models.payment import TimeBounds
        tb = TimeBounds(min_time=100, max_time=200)
        self.assertEqual(tb.min_time, 100)
        self.assertEqual(tb.max_time, 200)

    def test_time_bounds_5_minute_window(self):
        """Timeout window should be exactly 300 seconds."""
        from app.models.payment import TimeBounds, TRANSACTION_TIMEOUT_MAX_SECONDS
        now = datetime(2026, 6, 15, 0, 0, 0, tzinfo=timezone.utc)
        tb = TimeBounds.default_for_transaction(now_utc=now)
        window_seconds = tb.max_time - int(now.timestamp())
        self.assertEqual(window_seconds, 300)
        self.assertEqual(TRANSACTION_TIMEOUT_MAX_SECONDS, 300)


class TestPaymentTransactionTimeBounds(unittest.TestCase):
    """Test PaymentTransaction model includes time bounds fields."""

    def test_payment_transaction_has_time_bounds_fields(self):
        """PaymentTransaction should have time_bounds_min, time_bounds_max, etc."""
        from app.models.payment import PaymentTransaction
        tx = PaymentTransaction(
            id="pay_test1",
            transaction_hash="tx-test1",
            type="reward",
            amount=100.0,
            asset_code="USDC",
            from_address="SYSTEM_POOL",
            to_address="OUTAGE_SETTLEMENT",
            status="pending",
            outage_id="outage-001",
            created_at=datetime.now(timezone.utc),
        )
        self.assertEqual(tx.time_bounds_min, 0)
        self.assertEqual(tx.time_bounds_max, 0)
        self.assertFalse(tx.fee_re_estimation_pending)
        self.assertIsNone(tx.expired_at)

    def test_payment_transaction_with_time_bounds(self):
        """PaymentTransaction should accept time bounds values."""
        from app.models.payment import PaymentTransaction
        tx = PaymentTransaction(
            id="pay_test2",
            transaction_hash="tx-test2",
            type="reward",
            amount=200.0,
            asset_code="USDC",
            from_address="SYSTEM_POOL",
            to_address="OUTAGE_SETTLEMENT",
            status="pending",
            outage_id="outage-001",
            created_at=datetime.now(timezone.utc),
            time_bounds_min=0,
            time_bounds_max=1735689600,
            fee_re_estimation_pending=True,
        )
        self.assertEqual(tx.time_bounds_min, 0)
        self.assertEqual(tx.time_bounds_max, 1735689600)
        self.assertTrue(tx.fee_re_estimation_pending)


class TestTransactionExpiryService(unittest.TestCase):
    """Test TransactionExpiryService behavior."""

    def _make_orm(self, **overrides):
        """Create a mock PaymentTransactionORM with sensible defaults."""
        defaults = {
            "id": "pay_test1",
            "transaction_hash": "tx-test1",
            "type": "reward",
            "amount": 100.0,
            "asset_code": "USDC",
            "from_address": "SYSTEM_POOL",
            "to_address": "OUTAGE_SETTLEMENT",
            "status": "pending",
            "outage_id": "outage-001",
            "created_at": datetime.now(timezone.utc),
            "time_bounds_min": 0,
            "time_bounds_max": 0,
            "fee_re_estimation_pending": 0,
            "expired_at": None,
            "retry_count": 0,
            "last_retried_at": None,
            "failure_taxonomy": None,
            "dead_letter_reason": None,
            "dead_lettered_at": None,
            "confirmed_at": None,
            "idempotency_key": None,
            "sla_result_id": None,
        }
        defaults.update(overrides)
        orm = Mock()
        for k, v in defaults.items():
            setattr(orm, k, v)
        return orm

    @patch("app.services.transaction_expiry.datetime")
    def test_check_and_expire_not_expired_when_within_window(self, mock_dt):
        """Transaction should not expire when time_bounds_max is in the future."""
        from app.services.transaction_expiry import TransactionExpiryService
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = now
        mock_dt.fromtimestamp = datetime.fromtimestamp

        orm = self._make_orm(
            time_bounds_max=int(now.timestamp()) + 300,
        )
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = orm

        svc = TransactionExpiryService(db)
        result = svc.check_and_expire("pay_test1")
        self.assertIsNone(result)

    @patch("app.services.transaction_expiry.datetime")
    def test_check_and_expire_expires_when_past_max(self, mock_dt):
        """Transaction should expire when time_bounds_max has been exceeded."""
        from app.services.transaction_expiry import TransactionExpiryService

        now = datetime(2026, 1, 1, 12, 5, 1, tzinfo=timezone.utc)
        mock_dt.now.return_value = now
        mock_dt.fromtimestamp = datetime.fromtimestamp

        past_time = int((now - timedelta(seconds=400)).timestamp())
        orm = self._make_orm(time_bounds_max=past_time)
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = orm

        svc = TransactionExpiryService(db)
        result = svc.check_and_expire("pay_test1")

        self.assertTrue(orm.fee_re_estimation_pending)
        self.assertEqual(orm.expired_at, now)
        self.assertIn("time_bounds_expired", orm.failure_taxonomy)
        db.commit.assert_called_once()

    def test_check_and_expire_skips_non_pending(self):
        """Non-pending transactions should not be expired."""
        from app.services.transaction_expiry import TransactionExpiryService

        orm = self._make_orm(status="confirmed")
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = orm

        svc = TransactionExpiryService(db)
        result = svc.check_and_expire("pay_test1")
        self.assertIsNone(result)

    def test_check_and_expire_skips_no_time_bounds(self):
        """Transactions with time_bounds_max=0 should not be expired."""
        from app.services.transaction_expiry import TransactionExpiryService

        orm = self._make_orm(time_bounds_max=0)
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = orm

        svc = TransactionExpiryService(db)
        result = svc.check_and_expire("pay_test1")
        self.assertIsNone(result)

    def test_check_and_expire_skips_not_found(self):
        """Non-existent transaction should return None."""
        from app.services.transaction_expiry import TransactionExpiryService

        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = None

        svc = TransactionExpiryService(db)
        result = svc.check_and_expire("pay_nonexistent")
        self.assertIsNone(result)

    @patch("app.services.transaction_expiry.datetime")
    def test_expire_all_stale(self, mock_dt):
        """expire_all_stale should find and expire all stale pending transactions."""
        from app.services.transaction_expiry import TransactionExpiryService

        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = now

        orm1 = self._make_orm(id="pay_1", time_bounds_max=int(now.timestamp()) - 100)
        orm2 = self._make_orm(id="pay_2", time_bounds_max=int(now.timestamp()) - 200)
        db = Mock()
        db.query.return_value.filter.return_value.all.return_value = [orm1, orm2]

        svc = TransactionExpiryService(db)
        expired_ids = svc.expire_all_stale()

        self.assertEqual(len(expired_ids), 2)
        self.assertIn("pay_1", expired_ids)
        self.assertIn("pay_2", expired_ids)
        self.assertTrue(orm1.fee_re_estimation_pending)
        self.assertTrue(orm2.fee_re_estimation_pending)
        db.commit.assert_called_once()

    def test_expire_all_stale_empty_when_no_stale(self):
        """expire_all_stale should return empty list when no stale transactions."""
        from app.services.transaction_expiry import TransactionExpiryService

        db = Mock()
        db.query.return_value.filter.return_value.all.return_value = []

        svc = TransactionExpiryService(db)
        expired_ids = svc.expire_all_stale()
        self.assertEqual(expired_ids, [])

    def test_requeue_for_fee_estimation(self):
        """requeue should reset time bounds and clear expiry flags."""
        from app.services.transaction_expiry import TransactionExpiryService

        orm = self._make_orm(
            fee_re_estimation_pending=1,
            expired_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            retry_count=0,
        )
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = orm

        svc = TransactionExpiryService(db)
        result = svc.requeue_for_fee_estimation("pay_test1")

        self.assertIsNotNone(result)
        self.assertEqual(orm.fee_re_estimation_pending, 0)
        self.assertIsNone(orm.expired_at)
        self.assertEqual(orm.retry_count, 1)
        self.assertTrue(orm.time_bounds_max > 0)
        db.commit.assert_called_once()

    def test_requeue_skips_when_not_pending(self):
        """requeue should return None when fee_re_estimation_pending is False."""
        from app.services.transaction_expiry import TransactionExpiryService

        orm = self._make_orm(fee_re_estimation_pending=0)
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = orm

        svc = TransactionExpiryService(db)
        result = svc.requeue_for_fee_estimation("pay_test1")
        self.assertIsNone(result)

    def test_requeue_skips_not_found(self):
        """requeue should return None for non-existent transaction."""
        from app.services.transaction_expiry import TransactionExpiryService

        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = None

        svc = TransactionExpiryService(db)
        result = svc.requeue_for_fee_estimation("pay_nonexistent")
        self.assertIsNone(result)

    def test_apply_time_bounds_sets_default_window(self):
        """apply_time_bounds should set min=0 and max=now+300."""
        from app.services.transaction_expiry import TransactionExpiryService
        from app.models.payment import PaymentTransaction

        db = Mock()
        svc = TransactionExpiryService(db)

        tx = PaymentTransaction(
            id="pay_test",
            transaction_hash="tx-test",
            type="reward",
            amount=100.0,
            asset_code="USDC",
            from_address="SYSTEM_POOL",
            to_address="OUTAGE_SETTLEMENT",
            status="pending",
            outage_id="outage-001",
            created_at=datetime.now(timezone.utc),
        )

        result = svc.apply_time_bounds(tx)
        self.assertEqual(result.time_bounds_min, 0)
        self.assertGreater(result.time_bounds_max, 0)


if __name__ == "__main__":
    unittest.main()
