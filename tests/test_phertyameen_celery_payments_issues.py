"""Tests for Stellar Wave issues #540, #541, #542, #560.

- #560: deterministic Stellar payment idempotency key + duplicate rejection
- #542: Celery task payload sanitization (mask secret/private_key/token)
- #541: graceful SIGTERM shutdown handling (in-flight tasks re-queued)
- #540: priority task queue routing (high_priority / default / bulk)

All tests are offline. Celery conf is inspected directly (task config does not
require a live broker in eager/test mode).
"""
import logging
import unittest
from unittest.mock import patch

from app.core.config import settings
from app.models.payment import PaymentIdempotencyError
from app.repositories.payment_repository import PaymentRepository
from app.services.stellar_payments import generate_payment_idempotency_key
from app.tasks.celery_app import (
    _handle_sigterm,
    _sanitize_task_args,
    celery_app,
)


# --------------------------------------------------------------------------- #
# Issue #560 — deterministic payment idempotency key + duplicate rejection      #
# --------------------------------------------------------------------------- #


class TestPaymentIdempotencyKey(unittest.TestCase):
    """Issue #560 — generator produces deterministic, distinct keys."""

    def test_same_inputs_produce_same_key(self):
        key_a = generate_payment_idempotency_key("out-1", 100.0, "G_RECIPIENT")
        key_b = generate_payment_idempotency_key("out-1", 100.0, "G_RECIPIENT")
        self.assertEqual(key_a, key_b)
        # A SHA-256 hex digest is 64 hex characters.
        self.assertEqual(len(key_a), 64)

    def test_different_inputs_produce_different_keys(self):
        key_a = generate_payment_idempotency_key("out-1", 100.0, "G_A")
        key_b = generate_payment_idempotency_key("out-1", 100.0, "G_B")
        key_c = generate_payment_idempotency_key("out-1", 200.0, "G_A")
        key_d = generate_payment_idempotency_key("out-2", 100.0, "G_A")
        self.assertNotEqual(key_a, key_b)
        self.assertNotEqual(key_a, key_c)
        self.assertNotEqual(key_a, key_d)

    def test_repository_generator_delegates(self):
        repo = PaymentRepository.__new__(PaymentRepository)
        self.assertEqual(
            repo.generate_idempotency_key("out-1", 50.0, "G_X"),
            generate_payment_idempotency_key("out-1", 50.0, "G_X"),
        )


class TestPaymentIdempotencyDedup(unittest.TestCase):
    """Issue #560 — duplicate payment attempts are rejected."""

    def test_ensure_unique_returns_key_when_no_duplicate(self):
        repo = PaymentRepository(_FakeSession())
        with patch.object(
            repo, "get_by_idempotency_key", return_value=None
        ):
            key = repo.ensure_unique_idempotency_key(
                outage_id="out-1", amount=100.0, recipient="G_R"
            )
        self.assertEqual(key, generate_payment_idempotency_key("out-1", 100.0, "G_R"))

    def test_ensure_unique_raises_on_active_duplicate(self):
        dup_key = generate_payment_idempotency_key("out-1", 100.0, "G_R")
        repo = PaymentRepository(_FakeSession())
        existing = _payment_with_key(dup_key)
        with patch.object(
            repo, "get_by_idempotency_key", return_value=existing
        ):
            with self.assertRaises(PaymentIdempotencyError) as ctx:
                repo.ensure_unique_idempotency_key(
                    outage_id="out-1", amount=100.0, recipient="G_R"
                )
        self.assertEqual(ctx.exception.idempotency_key, dup_key)

    def test_ensure_unique_returns_existing_key_verbatim_when_free(self):
        repo = PaymentRepository(_FakeSession())
        with patch.object(
            repo, "get_by_idempotency_key", return_value=None
        ):
            key = repo.ensure_unique_idempotency_key(
                outage_id="out-1",
                amount=100.0,
                recipient="G_R",
                existing_key="legacy_key",
            )
        self.assertEqual(key, "legacy_key")

    def test_get_by_idempotency_key_returns_matching_payment(self):
        from app.models.payment import PaymentTransaction

        key = generate_payment_idempotency_key("out-9", 10.0, "G_Z")
        fake_db = _FakeSession(match_key=key)
        repo = PaymentRepository(fake_db)
        result = repo.get_by_idempotency_key(key)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, PaymentTransaction)
        self.assertEqual(result.idempotency_key, key)

    def test_get_by_idempotency_key_returns_none_when_missing(self):
        fake_db = _FakeSession()
        repo = PaymentRepository(fake_db)
        self.assertIsNone(repo.get_by_idempotency_key("nonexistent-key"))

    def test_idempotency_error_subclasses_value_error(self):
        self.assertTrue(issubclass(PaymentIdempotencyError, ValueError))


# --------------------------------------------------------------------------- #
# Issue #542 — task payload sanitization                                       #
# --------------------------------------------------------------------------- #


class TestTaskPayloadSanitization(unittest.TestCase):
    """Issue #542 — secret/private_key/token are masked, other values intact."""

    def test_sanitizes_top_level_sensitive_keys(self):
        safe_args, safe_kwargs = _sanitize_task_args(
            (),
            {
                "delivery_id": "abc",
                "api_secret": "s3cret",
                "private_key": "PKEY",
                "refresh_token": "tok",
            },
        )
        self.assertEqual(safe_kwargs["delivery_id"], "abc")
        self.assertEqual(safe_kwargs["api_secret"], "[REDACTED]")
        self.assertEqual(safe_kwargs["private_key"], "[REDACTED]")
        self.assertEqual(safe_kwargs["refresh_token"], "[REDACTED]")

    def test_sanitizes_nested_structures_recursively(self):
        safe_args, safe_kwargs = _sanitize_task_args(
            [
                {"account": "G_A", "secret_key": "hide-me",
                 "nested": {"token": "deep", "tenant_id": 5}},
            ],
            {},
        )
        nested_safe = safe_args[0]
        self.assertEqual(nested_safe["account"], "G_A")
        self.assertEqual(nested_safe["secret_key"], "[REDACTED]")
        self.assertEqual(nested_safe["nested"]["token"], "[REDACTED]")
        self.assertEqual(nested_safe["nested"]["tenant_id"], 5)

    def test_other_values_remain_intact(self):
        safe_args, safe_kwargs = _sanitize_task_args(
            ("dev-1", "2026-01"),
            {"device_id": "dev-1", "amount": 100.5},
        )
        # Tuples/lists keep their element values (converted to a list), and
        # non-sensitive scalar kwargs are unchanged.
        self.assertEqual(safe_args, ["dev-1", "2026-01"])
        self.assertEqual(safe_kwargs, {"device_id": "dev-1", "amount": 100.5})

    def test_scalar_values_are_returned_unchanged(self):
        safe_args, safe_kwargs = _sanitize_task_args("just-a-string", None)
        self.assertEqual(safe_args, "just-a-string")
        self.assertIsNone(safe_kwargs)

    def test_mask_respects_settings(self):
        with patch.object(settings, "WEBHOOK_REDACTION_MASK", "XXX"):
            _, safe_kwargs = _sanitize_task_args((), {"token": "t"})
            self.assertEqual(safe_kwargs["token"], "XXX")


# --------------------------------------------------------------------------- #
# Issue #541 — graceful SIGTERM shutdown handling                              #
# --------------------------------------------------------------------------- #


class TestGracefulSigtermShutdown(unittest.TestCase):
    """Issue #541 — SIGTERM handler exists, is callable, and logs gracefully."""

    def test_ack_late_configured(self):
        self.assertTrue(celery_app.conf.task_acks_late)

    def test_cancel_long_running_on_connection_loss_configured(self):
        self.assertTrue(
            celery_app.conf.worker_cancel_long_running_tasks_on_connection_loss
        )

    def test_sigterm_handler_exists_and_is_callable(self):
        self.assertTrue(callable(_handle_sigterm))

    def test_sigterm_handler_logs_without_exception(self):
        with self.assertLogs("app.tasks.celery_app", level=logging.WARNING) as logs:
            _handle_sigterm(15, None)
        self.assertTrue(
            any("SIGTERM received" in msg for msg in logs.output)
        )


# --------------------------------------------------------------------------- #
# Issue #540 — priority task queue routing                                     #
# --------------------------------------------------------------------------- #


class TestPriorityQueueRouting(unittest.TestCase):
    """Issue #540 — three queues exist and urgent webhooks route to high priority."""

    def test_three_queues_configured(self):
        queues = getattr(celery_app.conf, "task_queues", ())
        queue_names = [q.name for q in queues]
        self.assertEqual(
            sorted(queue_names), ["bulk", "default", "high_priority"]
        )

    def test_default_queue_is_default(self):
        self.assertEqual(celery_app.conf.task_default_queue, "default")

    def test_dispatch_webhook_delivery_routed_to_high_priority(self):
        routes = getattr(celery_app.conf, "task_routes", {})
        route = routes.get("app.tasks.webhook_tasks.dispatch_webhook_delivery", {})
        self.assertEqual(route.get("queue"), "high_priority")


class _FakeSession:
    """Minimal stand-in for ``PaymentRepository.get_by_idempotency_key``.

    ``query().filter().first()`` returns an ORM-like payment row (mapped via
    the repository's ``_orm_to_pydantic``) when ``match_key`` is set, else
    None. Each dedup test uses a single key, so the filter itself need not be
    inspected.
    """

    def __init__(self, match_key=None):
        self.match_key = match_key

    def query(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        if self.match_key is not None:
            from datetime import datetime, timezone

            from app.models.orm.payment import PaymentTransactionORM

            return PaymentTransactionORM(
                id="pay_existing",
                transaction_hash="tx-exists",
                type="reward",
                amount=100.0,
                asset_code="USDC",
                from_address="SYSTEM_POOL",
                to_address="G_R",
                status="pending",
                outage_id="out-1",
                created_at=datetime.now(timezone.utc),
                idempotency_key=self.match_key,
                retry_count=0,
                time_bounds_min=0,
                time_bounds_max=0,
                fee_re_estimation_pending=0,
            )
        return None


def _payment_with_key(key):
    """Build a ``PaymentTransaction`` bearing ``key`` (for dedup tests)."""
    from datetime import datetime, timezone

    from app.models.payment import PaymentTransaction

    return PaymentTransaction(
        id="pay_existing",
        transaction_hash="tx-exists",
        type="reward",
        amount=100.0,
        asset_code="USDC",
        from_address="SYSTEM_POOL",
        to_address="G_R",
        status="pending",
        outage_id="out-1",
        created_at=datetime.now(timezone.utc),
        idempotency_key=key,
    )


if __name__ == "__main__":
    unittest.main()
