"""
Tests for issues #224, #226, #227, #229.

BE-031 (#229) – Wallet address validation and normalization pipeline
BE-029 (#227) – DB-level duplicate-payment protection under concurrency
BE-028 (#226) – Authenticated inbound provider callbacks with replay protection
BE-026 (#224) – Payment status transition rules enforced centrally
"""
from __future__ import annotations

import hashlib
import hmac
import threading
import time
from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

# ---------------------------------------------------------------------------
# BE-031 – Wallet address validation / normalization
# ---------------------------------------------------------------------------

from app.utils.wallet_address import normalize, is_valid, WalletAddressError, NormalizedAddress
from app.models.wallet import WalletLinkRequest


class TestWalletNormalizePipeline:
    VALID_KEY = "GBKJLJHNJFGFGC2TMMVNZJ3NXKGHCB37BRT7FGJKFHB3NJKFHB3NJK2"

    def _make_valid_key(self) -> str:
        # 'G' + 55 chars from A-Z / 2-7
        return "G" + "A" * 55

    def test_valid_address_returns_normalized(self):
        key = self._make_valid_key()
        result = normalize(key)
        assert isinstance(result, NormalizedAddress)
        assert str(result) == key

    def test_lowercase_input_is_uppercased(self):
        key = self._make_valid_key()
        result = normalize(key.lower())
        assert str(result) == key.upper()

    def test_whitespace_stripped(self):
        key = self._make_valid_key()
        result = normalize(f"  {key}  ")
        assert str(result) == key

    def test_mixed_case_and_whitespace_normalized(self):
        key = self._make_valid_key()
        # lowercase with spaces should produce the same canonical form
        result1 = normalize(key)
        result2 = normalize("  " + key.lower() + "  ")
        assert str(result1) == str(result2)

    def test_too_short_raises(self):
        with pytest.raises(WalletAddressError, match="exactly 56"):
            normalize("GABC")

    def test_too_long_raises(self):
        with pytest.raises(WalletAddressError, match="exactly 56"):
            normalize("G" + "A" * 60)

    def test_not_starting_with_g_raises(self):
        bad = "A" + "A" * 55
        with pytest.raises(WalletAddressError, match="start with 'G'"):
            normalize(bad)

    def test_invalid_chars_raise(self):
        # '0' and '1' and '8' are not in Stellar base-32
        bad = "G" + "0" * 55
        with pytest.raises(WalletAddressError):
            normalize(bad)

    def test_empty_string_raises(self):
        with pytest.raises(WalletAddressError, match="empty"):
            normalize("")

    def test_non_string_raises(self):
        with pytest.raises(WalletAddressError):
            normalize(12345)  # type: ignore[arg-type]

    def test_is_valid_true_for_good_key(self):
        assert is_valid(self._make_valid_key()) is True

    def test_is_valid_false_for_bad_key(self):
        assert is_valid("NOTAKEY") is False

    def test_wallet_link_request_normalizes(self):
        key = self._make_valid_key()
        req = WalletLinkRequest(user_id="u1", public_key=key.lower())
        assert req.public_key == key.upper()

    def test_wallet_link_request_rejects_malformed(self):
        with pytest.raises(Exception):
            WalletLinkRequest(user_id="u1", public_key="BADKEY")

    def test_equivalent_addresses_produce_same_canonical(self):
        """Mixed-case variants of the same address must normalize to one form."""
        key = self._make_valid_key()
        variants = [key, key.lower(), key.swapcase(), f"  {key}  "]
        normalized = {str(normalize(v)) for v in variants}
        assert len(normalized) == 1, "All variants must normalize to the same canonical key"


# ---------------------------------------------------------------------------
# BE-026 (#224) – Central payment status transition rules
# ---------------------------------------------------------------------------

from app.models.payment import (
    PaymentStatus,
    VALID_TRANSITIONS,
    validate_transition,
    PaymentTransitionError,
)


class TestPaymentStatusTransitions:

    def test_pending_to_confirmed_allowed(self):
        validate_transition("pending", "confirmed")  # should not raise

    def test_pending_to_failed_allowed(self):
        validate_transition("pending", "failed")

    def test_failed_to_pending_allowed(self):
        validate_transition("failed", "pending")

    def test_confirmed_to_pending_forbidden(self):
        with pytest.raises(PaymentTransitionError) as exc_info:
            validate_transition("confirmed", "pending")
        err = exc_info.value
        assert err.current == "confirmed"
        assert err.next_status == "pending"
        assert isinstance(err.allowed, set)

    def test_confirmed_to_failed_forbidden(self):
        with pytest.raises(PaymentTransitionError):
            validate_transition("confirmed", "failed")

    def test_pending_to_pending_forbidden(self):
        with pytest.raises(PaymentTransitionError):
            validate_transition("pending", "pending")

    def test_invalid_status_raises(self):
        with pytest.raises(PaymentTransitionError):
            validate_transition("pending", "nonexistent")

    def test_error_carries_structured_data(self):
        with pytest.raises(PaymentTransitionError) as exc_info:
            validate_transition("confirmed", "pending")
        err = exc_info.value
        assert err.current == "confirmed"
        assert err.next_status == "pending"
        # confirmed → allowed transitions is empty set
        assert err.allowed == set()

    def test_valid_transitions_table_is_exhaustive(self):
        """Every PaymentStatus must appear in VALID_TRANSITIONS."""
        for status in PaymentStatus:
            assert status in VALID_TRANSITIONS, f"{status} missing from VALID_TRANSITIONS"

    def test_validate_transition_is_used_by_repository(self):
        """update_status in the repo must call validate_transition."""
        from app.repositories.payment_repository import PaymentRepository
        from app.models.orm.payment import PaymentTransactionORM

        mock_orm = MagicMock(spec=PaymentTransactionORM)
        mock_orm.status = "confirmed"

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_orm

        repo = PaymentRepository(mock_db)

        with pytest.raises(PaymentTransitionError):
            repo.update_status("tx-1", "pending")  # confirmed → pending is forbidden

    def test_reconcile_calls_validate_transition(self):
        from app.repositories.payment_repository import PaymentRepository
        from app.models.orm.payment import PaymentTransactionORM

        mock_orm = MagicMock(spec=PaymentTransactionORM)
        mock_orm.status = "confirmed"

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_orm

        repo = PaymentRepository(mock_db)
        with pytest.raises(PaymentTransitionError):
            repo.reconcile("tx-1", "failed")  # confirmed → failed is forbidden

    def test_retry_calls_validate_transition(self):
        from app.repositories.payment_repository import PaymentRepository
        from app.models.orm.payment import PaymentTransactionORM

        mock_orm = MagicMock(spec=PaymentTransactionORM)
        mock_orm.status = "confirmed"
        mock_orm.retry_count = 0

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_orm

        repo = PaymentRepository(mock_db)
        with pytest.raises(PaymentTransitionError):
            repo.retry("tx-1")  # confirmed → pending is forbidden


# ---------------------------------------------------------------------------
# BE-028 (#226) – Provider callbacks with replay protection
# ---------------------------------------------------------------------------

from app.api.v1.endpoints.payments import (
    _verify_callback_signature,
    _is_replay,
    _SEEN_NONCES,
    CALLBACK_NONCE_TTL_SECONDS,
)


class TestCallbackSignatureVerification:
    SECRET = "test_secret_123"

    def _make_sig(self, txid: str, status: str, nonce: str | None) -> str:
        message = f"{txid}:{status}:{nonce or ''}"
        return hmac.new(self.SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()

    def test_valid_signature_accepted(self):
        nonce = "abc123"
        sig = self._make_sig("tx-1", "confirmed", nonce)
        assert _verify_callback_signature("tx-1", "confirmed", nonce, sig, self.SECRET)

    def test_wrong_signature_rejected(self):
        assert not _verify_callback_signature("tx-1", "confirmed", "nonce", "badhex", self.SECRET)

    def test_tampered_status_rejected(self):
        nonce = "n1"
        sig = self._make_sig("tx-1", "confirmed", nonce)
        # attacker changes status to "failed" but keeps the original signature
        assert not _verify_callback_signature("tx-1", "failed", nonce, sig, self.SECRET)

    def test_nonce_is_part_of_signature(self):
        """Changing the nonce invalidates the signature."""
        sig = self._make_sig("tx-1", "confirmed", "original-nonce")
        assert not _verify_callback_signature("tx-1", "confirmed", "other-nonce", sig, self.SECRET)

    def test_no_nonce_signature(self):
        """Callbacks without nonce still validate correctly."""
        sig = self._make_sig("tx-1", "confirmed", None)
        assert _verify_callback_signature("tx-1", "confirmed", None, sig, self.SECRET)


class TestReplayProtection:
    def setup_method(self):
        _SEEN_NONCES.clear()

    def test_first_use_not_replay(self):
        assert not _is_replay(f"nonce-{uuid4().hex}")

    def test_second_use_is_replay(self):
        nonce = f"nonce-{uuid4().hex}"
        _is_replay(nonce)  # first use – registers it
        assert _is_replay(nonce)  # second use – should be detected as replay

    def test_distinct_nonces_not_replay(self):
        assert not _is_replay(f"nonce-a-{uuid4().hex}")
        assert not _is_replay(f"nonce-b-{uuid4().hex}")

    def test_stale_nonces_evicted(self):
        nonce = f"nonce-{uuid4().hex}"
        _SEEN_NONCES[nonce] = time.monotonic() - CALLBACK_NONCE_TTL_SECONDS - 1
        # After eviction the nonce should be accepted again
        assert not _is_replay(nonce)

    def test_concurrent_nonce_use(self):
        """Two threads racing on the same nonce – exactly one should win."""
        nonce = f"concurrent-{uuid4().hex}"
        results = []
        lock = threading.Lock()

        def try_nonce():
            result = _is_replay(nonce)
            with lock:
                results.append(result)

        threads = [threading.Thread(target=try_nonce) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one call must have seen False (first use); the rest True (replay)
        assert results.count(False) == 1
        assert results.count(True) == 9


# ---------------------------------------------------------------------------
# BE-029 (#227) – Duplicate payment protection
# ---------------------------------------------------------------------------

class TestDuplicatePaymentProtection:
    """Unit tests for the deduplication logic in PaymentRepository."""

    def _make_sla_result(self):
        from tests.factories import make_sla_result
        return make_sla_result({"id": 99, "outage_id": "outage-dup-test", "payment_type": "reward"})

    def test_create_for_sla_result_returns_existing_on_duplicate(self):
        """If a row already exists for the sla_result_id, return it without inserting."""
        from app.repositories.payment_repository import PaymentRepository

        existing_orm = MagicMock()
        existing_orm.id = "pay_existing"
        existing_orm.transaction_hash = "sla-99-reward"
        existing_orm.type = "reward"
        existing_orm.amount = 100.0
        existing_orm.asset_code = "USDC"
        existing_orm.from_address = "SYSTEM_POOL"
        existing_orm.to_address = "OUTAGE_SETTLEMENT"
        existing_orm.status = "pending"
        existing_orm.outage_id = "outage-dup-test"
        existing_orm.sla_result_id = 99
        existing_orm.created_at = datetime.utcnow()
        existing_orm.confirmed_at = None
        existing_orm.retry_count = 0
        existing_orm.last_retried_at = None

        mock_db = MagicMock()
        # FOR UPDATE query returns the existing row
        mock_db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = existing_orm

        repo = PaymentRepository(mock_db)
        sla = self._make_sla_result()
        result = repo.create_for_sla_result("outage-dup-test", sla)

        assert result.id == "pay_existing"
        # create() must NOT be called when the record already exists
        mock_db.add.assert_not_called()

    def test_integrity_error_falls_back_to_select(self):
        """On IntegrityError (race condition), the repo falls back to SELECT."""
        from sqlalchemy.exc import IntegrityError
        from app.repositories.payment_repository import PaymentRepository

        existing_orm = MagicMock()
        existing_orm.id = "pay_winner"
        existing_orm.transaction_hash = "sla-99-reward"
        existing_orm.type = "reward"
        existing_orm.amount = 100.0
        existing_orm.asset_code = "USDC"
        existing_orm.from_address = "SYSTEM_POOL"
        existing_orm.to_address = "OUTAGE_SETTLEMENT"
        existing_orm.status = "pending"
        existing_orm.outage_id = "outage-dup-test"
        existing_orm.sla_result_id = 99
        existing_orm.created_at = datetime.utcnow()
        existing_orm.confirmed_at = None
        existing_orm.retry_count = 0
        existing_orm.last_retried_at = None

        call_count = {"n": 0}

        def query_side_effect(model):
            # First call (FOR UPDATE) returns nothing; second call (fallback) returns existing
            call_count["n"] += 1
            mock_q = MagicMock()
            mock_q.filter.return_value.with_for_update.return_value.first.return_value = None
            mock_q.filter.return_value.first.return_value = (
                existing_orm if call_count["n"] > 1 else None
            )
            return mock_q

        mock_db = MagicMock()
        mock_db.query.side_effect = query_side_effect
        mock_db.add.side_effect = None  # add doesn't raise
        mock_db.commit.side_effect = IntegrityError("duplicate key", {}, Exception())

        repo = PaymentRepository(mock_db)
        sla = self._make_sla_result()
        result = repo.create_for_sla_result("outage-dup-test", sla)

        # Should return the record inserted by the concurrent winner
        assert result.id == "pay_winner"

    def test_migration_defines_unique_constraint(self):
        """The deduplication migration must define the composite unique constraint."""
        import importlib.util, sys, pathlib

        migration_path = (
            pathlib.Path(__file__).parent.parent
            / "alembic"
            / "versions"
            / "0011_payment_deduplication.py"
        )
        assert migration_path.exists(), "Migration 0011_payment_deduplication.py must exist"

        source = migration_path.read_text()
        assert "uq_payment_outage_type" in source, \
            "Migration must define the uq_payment_outage_type unique constraint"
        assert "uq_payment_sla_result_id" in source, \
            "Migration must define the uq_payment_sla_result_id partial unique index"
