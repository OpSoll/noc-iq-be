"""Offline payment service unit tests using MockStellarClient.

Validates:
1. Payment creation with time bounds
2. Transaction submission via mock client
3. Balance queries without network dependencies
4. Payment status transitions
5. Dead-letter and replay flows

All tests execute offline — no live Horizon server connection required.
"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

from tests.mocks.stellar import MockStellarClient, MockStellarClientFactory, MockHorizonError


class TestMockStellarClient(unittest.TestCase):
    """Test the MockStellarClient itself to verify it works correctly."""

    def setUp(self):
        self.client = MockStellarClientFactory.for_testing()

    def test_configure_default_account(self):
        """configure_default_account should register an account."""
        client = MockStellarClient()
        account = client.configure_default_account("GTEST123")
        self.assertTrue(client.account_exists("GTEST123"))
        self.assertEqual(account.address, "GTEST123")

    def test_account_exists_returns_true_for_configured(self):
        """account_exists should return True for configured accounts."""
        self.assertTrue(self.client.account_exists("GABC1234567890ABCDEF"))

    def test_account_exists_returns_false_for_unknown(self):
        """account_exists should return False for unconfigured accounts."""
        self.assertFalse(self.client.account_exists("GUNKNOWN"))

    def test_get_account_balance_returns_balances(self):
        """get_account_balance should return account balances."""
        result = self.client.get_account_balance("GABC1234567890ABCDEF")
        self.assertIn("balances", result)
        self.assertEqual(len(result["balances"]), 2)
        self.assertEqual(result["balances"][0]["asset_type"], "native")
        self.assertEqual(result["balances"][1]["asset_code"], "USDC")

    def test_get_account_balance_raises_for_missing(self):
        """get_account_balance should raise for unknown accounts."""
        with self.assertRaises(MockHorizonError) as ctx:
            self.client.get_account_balance("GUNKNOWN")
        self.assertEqual(ctx.exception.status, 404)

    def test_submit_transaction_returns_success(self):
        """submit_transaction should return a successful result."""
        result = self.client.submit_transaction("base64_envelope_xdr_here")
        self.assertTrue(result.successful)
        self.assertEqual(len(result.hash), 64)
        self.assertEqual(result.fee_charged, 100)

    def test_submit_transaction_stores_history(self):
        """submit_transaction should store the transaction in history."""
        self.client.submit_transaction("tx1")
        self.client.submit_transaction("tx2")
        history = self.client.get_submitted_transactions()
        self.assertEqual(len(history), 2)

    def test_get_account_trustlines_ready(self):
        """get_account_trustlines should return ready for existing trustline."""
        result = self.client.get_account_trustlines(
            "GABC1234567890ABCDEF", "USDC", "TEST_ISSUER"
        )
        self.assertEqual(result["status"], "ready")
        self.assertIsNotNone(result["balance"])

    def test_get_account_trustlines_missing(self):
        """get_account_trustlines should return missing for unknown asset."""
        result = self.client.get_account_trustlines(
            "GABC1234567890ABCDEF", "EURC", "OTHER_ISSUER"
        )
        self.assertEqual(result["status"], "missing")

    def test_get_account_trustlines_missing_account(self):
        """get_account_trustlines should return missing for unknown account."""
        result = self.client.get_account_trustlines(
            "GUNKNOWN", "USDC", "TEST_ISSUER"
        )
        self.assertEqual(result["status"], "missing")

    def test_reset_clears_all_state(self):
        """reset should clear all accounts and submitted transactions."""
        self.client.submit_transaction("tx1")
        self.assertTrue(self.client.account_exists("GABC1234567890ABCDEF"))
        self.client.reset()
        self.assertFalse(self.client.account_exists("GABC1234567890ABCDEF"))
        self.assertEqual(len(self.client.get_submitted_transactions()), 0)

    def test_submit_transaction_expect_failure(self):
        """submit_transaction_expect_failure should return an error."""
        error = self.client.submit_transaction_expect_failure("bad_tx")
        self.assertEqual(error.status, 400)
        self.assertIn("Transaction Failed", error.title)


class TestMockStellarClientFactory(unittest.TestCase):
    """Test factory methods for pre-configured clients."""

    def test_for_testing_has_two_accounts(self):
        """for_testing should create a client with two configured accounts."""
        client = MockStellarClientFactory.for_testing()
        self.assertTrue(client.account_exists("GABC1234567890ABCDEF"))
        self.assertTrue(client.account_exists("GDEF9876543210FEDCBA"))

    def test_for_empty_account_has_zero_balance(self):
        """for_empty_account should have zero native balance."""
        client = MockStellarClientFactory.for_empty_account()
        result = client.get_account_balance("GEMPTY00000000000000")
        self.assertEqual(result["balances"][0]["balance"], "0.0000000")

    def test_for_missing_account_has_no_accounts(self):
        """for_missing_account should have no accounts configured."""
        client = MockStellarClientFactory.for_missing_account()
        self.assertFalse(client.account_exists("GANYTHING"))


class TestPaymentCreationOffline(unittest.TestCase):
    """Test payment creation and model behavior offline."""

    def test_payment_with_time_bounds(self):
        """PaymentTransaction should accept time bounds fields."""
        from app.models.payment import PaymentTransaction

        now = datetime.now(timezone.utc)
        tx = PaymentTransaction(
            id=f"pay_{uuid4().hex[:12]}",
            transaction_hash=f"tx-{uuid4().hex[:16]}",
            type="reward",
            amount=150.0,
            asset_code="USDC",
            from_address="GABC1234567890ABCDEF",
            to_address="GDEF9876543210FEDCBA",
            status="pending",
            outage_id="outage-001",
            created_at=now,
            time_bounds_min=0,
            time_bounds_max=int(now.timestamp()) + 300,
        )
        self.assertEqual(tx.time_bounds_min, 0)
        self.assertGreater(tx.time_bounds_max, 0)
        self.assertEqual(tx.status, "pending")

    def test_payment_retry_flow(self):
        """Payment retry should increment retry_count."""
        from app.models.payment import PaymentTransaction

        tx = PaymentTransaction(
            id="pay_retry1",
            transaction_hash="tx-retry1",
            type="settlement",
            amount=250.0,
            asset_code="USDC",
            from_address="SYSTEM_POOL",
            to_address="OUTAGE_SETTLEMENT",
            status="pending",
            outage_id="outage-002",
            created_at=datetime.now(timezone.utc),
        )
        self.assertEqual(tx.retry_count, 0)
        tx.retry_count += 1
        self.assertEqual(tx.retry_count, 1)


class TestPaymentOfflineSubmission(unittest.TestCase):
    """Test payment submission using the mock Stellar client."""

    def setUp(self):
        self.client = MockStellarClientFactory.for_testing()

    def test_submit_payment_transaction(self):
        """Should submit a transaction envelope via mock client."""
        envelope_xdr = "AAAAAGABC1234567890ABCDEF..."
        result = self.client.submit_transaction(envelope_xdr)
        self.assertTrue(result.successful)
        self.assertEqual(result.fee_charged, 100)

    def test_submit_payment_stores_hash(self):
        """Submitted transaction should have a deterministic hash."""
        result = self.client.submit_transaction("test_envelope")
        self.assertEqual(len(result.hash), 64)
        # Same envelope should produce same hash
        result2 = self.client.submit_transaction("test_envelope")
        self.assertEqual(result.hash, result2.hash)

    def test_balance_query_after_submission(self):
        """Balance query should still work after transaction submission."""
        self.client.submit_transaction("tx1")
        balance = self.client.get_account_balance("GABC1234567890ABCDEF")
        self.assertIn("balances", balance)

    def test_account_balance_for_usdc(self):
        """Should return USDC balance for configured account."""
        balance = self.client.get_account_balance("GABC1234567890ABCDEF")
        usdc_balances = [
            b for b in balance["balances"]
            if b.get("asset_code") == "USDC"
        ]
        self.assertEqual(len(usdc_balances), 1)
        self.assertEqual(usdc_balances[0]["balance"], "500.0000000")

    def test_account_balance_for_unfunded(self):
        """Unfunded account should have zero native balance."""
        balance = self.client.get_account_balance("GDEF9876543210FEDCBA")
        native = [b for b in balance["balances"] if b["asset_type"] == "native"]
        self.assertEqual(len(native), 1)
        self.assertEqual(native[0]["balance"], "0.0000000")


class TestPaymentStatusTransitionsOffline(unittest.TestCase):
    """Test payment status transitions offline."""

    def test_valid_transitions(self):
        """Valid status transitions should be accepted."""
        from app.models.payment import validate_transition, PaymentTransitionError

        # pending -> confirmed
        validate_transition("pending", "confirmed")
        # pending -> failed
        validate_transition("pending", "failed")

    def test_invalid_transition_raises(self):
        """Invalid status transitions should raise PaymentTransitionError."""
        from app.models.payment import validate_transition, PaymentTransitionError

        with self.assertRaises(PaymentTransitionError):
            validate_transition("confirmed", "pending")

    def test_dead_letter_to_pending_replay(self):
        """Dead letter -> pending should be valid for replay."""
        from app.models.payment import validate_transition
        validate_transition("dead_letter", "pending")


class TestPaymentDeadLetterOffline(unittest.TestCase):
    """Test dead-letter payment flows offline."""

    def test_dead_letter_fields(self):
        """PaymentTransaction should support dead-letter metadata."""
        from app.models.payment import PaymentTransaction

        tx = PaymentTransaction(
            id="pay_dl1",
            transaction_hash="tx-dl1",
            type="reward",
            amount=100.0,
            asset_code="USDC",
            from_address="SYSTEM_POOL",
            to_address="OUTAGE_SETTLEMENT",
            status="dead_letter",
            outage_id="outage-dl",
            created_at=datetime.now(timezone.utc),
            dead_letter_reason="Max retries exceeded",
            dead_lettered_at=datetime.now(timezone.utc),
        )
        self.assertEqual(tx.status, "dead_letter")
        self.assertEqual(tx.dead_letter_reason, "Max retries exceeded")
        self.assertIsNotNone(tx.dead_lettered_at)


if __name__ == "__main__":
    unittest.main()
