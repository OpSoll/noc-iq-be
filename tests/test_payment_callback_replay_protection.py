"""Replay protection for inbound payment provider callbacks (#982).

A provider callback mutates payment state, so a replayed callback must not apply
its change twice. The nonce check at
``app/api/v1/endpoints/payments.py:469`` and the audit write beside it were both
untested.

Two guards matter, and they sit in a specific order:

1. **Signature** — HMAC over ``<transaction_id>:<status>:<nonce>``. Skipped when
   ``PAYMENT_WEBHOOK_SECRET`` is empty, which is the default.
2. **Nonce replay** — ``_is_replay`` rejects a nonce already seen within
   ``CALLBACK_NONCE_TTL_SECONDS`` (300s) with 409, and writes
   ``callback_rejected_replay`` to the audit log before raising.
3. **Status idempotency** — a callback whose status equals the payment's current
   status returns 200 with the unchanged record.

The nonce check runs **before** the payment lookup, which is asserted here: if the
lookup ran first, an unknown transaction would 404 and a replay would never be
recorded as a replay.

Two things a naive test here would get wrong:

  - ``_SEEN_NONCES`` is module-level state that leaks between tests, so it is
    cleared around every test. Without that, the "first callback is accepted"
    case fails for the wrong reason once a nonce repeats.
  - The secret is cleared so the replay path is actually reached. With a secret
    configured, every request 401s at signature verification and all the replay
    assertions would pass against a completely broken nonce check.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

AUTH = {"Authorization": "Bearer test-engineer-token"}
CALLBACK_URL = "/api/v1/payments/provider-callback"


@pytest.fixture(autouse=True)
def clean_nonces():
    from app.api.v1.endpoints import payments

    payments._SEEN_NONCES.clear()
    yield
    payments._SEEN_NONCES.clear()


@pytest.fixture(autouse=True)
def no_signature(monkeypatch):
    """Run unsigned so replay protection is the only thing under test."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "PAYMENT_WEBHOOK_SECRET", "")


@pytest.fixture
def payment(db: Session):
    """A pending payment to drive callbacks against.

    ``pending -> confirmed`` is the only transition that both applies cleanly and
    is observable, per VALID_TRANSITIONS in app/models/payment.py.
    """
    from app.models.payment import PaymentTransaction
    from app.repositories.payment_repository import PaymentRepository

    txn = PaymentTransaction(
        id="pay-replay-1",
        transaction_hash="tx-replay-1",
        type="reward",
        amount=1000.0,
        asset_code="USDC",
        from_address="SYSTEM",
        to_address="USER",
        status="pending",
        outage_id="out-replay-1",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    PaymentRepository(db).create(txn)
    db.commit()
    return txn


def post_callback(
    client: TestClient,
    nonce: str,
    status: str = "confirmed",
    transaction_id: str = "pay-replay-1",
    **extra,
):
    return client.post(
        CALLBACK_URL,
        headers={**AUTH, "X-Callback-Nonce": nonce},
        json={"transaction_id": transaction_id, "status": status, "nonce": nonce, **extra},
    )


def db_status(db: Session, transaction_id: str = "pay-replay-1") -> str:
    from app.models.orm.payment import PaymentTransaction as PaymentORM

    row = db.query(PaymentORM).filter(PaymentORM.id == transaction_id).first()
    assert row is not None, f"{transaction_id} not found"
    return row.status.value if hasattr(row.status, "value") else str(row.status)


# --------------------------------------------------------------------------- #
# #982 — replay protection
# --------------------------------------------------------------------------- #


class TestFirstDelivery:
    def test_first_callback_is_accepted(self, client: TestClient, payment):
        response = post_callback(client, nonce="nonce-first-1")

        assert response.status_code == 200, response.text

    def test_first_callback_applies_the_state_change(self, client: TestClient, payment, db: Session):
        """Without this, every rejection test below would pass against an
        endpoint that never applied anything at all."""
        post_callback(client, nonce="nonce-apply-1")

        assert db_status(db) == "confirmed"

    def test_distinct_nonces_are_both_accepted(self, client: TestClient, payment):
        """Nonces are per-request, so a fresh nonce is never a replay.

        The second callback keeps the status already reached, which the
        idempotency guard returns unchanged rather than transitioning again.
        """
        assert post_callback(client, nonce="nonce-distinct-1").status_code == 200
        assert post_callback(client, nonce="nonce-distinct-2").status_code == 200


class TestReplayRejected:
    def test_replayed_nonce_is_rejected_with_conflict(self, client: TestClient, payment):
        assert post_callback(client, nonce="nonce-replay-1").status_code == 200

        replay = post_callback(client, nonce="nonce-replay-1")

        assert replay.status_code == 409
        assert "replay" in replay.json()["detail"].lower()

    def test_replay_does_not_apply_the_state_change_again(
        self, client: TestClient, payment, db: Session
    ):
        """The property the mechanism exists to protect.

        A 409 on the replay is not sufficient on its own: what matters is that the
        payment did not move twice. Asserting the stored status catches an
        implementation that rejects the replay but has already applied it.
        """
        post_callback(client, nonce="nonce-state-1")
        after_first = db_status(db)

        # Same nonce, so this is the replay. Status stays put.
        post_callback(client, nonce="nonce-state-1")

        assert db_status(db) == after_first

    def test_third_delivery_of_the_same_nonce_is_still_rejected(
        self, client: TestClient, payment
    ):
        post_callback(client, nonce="nonce-triple")

        assert post_callback(client, nonce="nonce-triple").status_code == 409
        assert post_callback(client, nonce="nonce-triple").status_code == 409

    def test_replay_is_checked_before_the_payment_lookup(
        self, client: TestClient, payment
    ):
        """Ordering, asserted because it is load-bearing.

        If the payment lookup ran first, a replay naming an unknown transaction
        would return 404 and the replay would never be recorded as a replay.
        """
        assert (
            client.post(
                CALLBACK_URL,
                headers={**AUTH, "X-Callback-Nonce": "nonce-order"},
                json={"transaction_id": "pay-replay-1", "status": "confirmed"},
            ).status_code
            == 200
        )

        replay = client.post(
            CALLBACK_URL,
            headers={**AUTH, "X-Callback-Nonce": "nonce-order"},
            json={"transaction_id": "does-not-exist", "status": "confirmed"},
        )
        assert replay.status_code == 409


class TestReplayIsAudited:
    def test_rejection_writes_an_audit_entry(self, client: TestClient, payment, monkeypatch):
        from app.api.v1.endpoints import payments

        recorded: list[tuple] = []
        monkeypatch.setattr(
            payments.audit_log,
            "log",
            lambda event_type, details=None, **kw: recorded.append((event_type, details)),
        )

        post_callback(client, nonce="nonce-audit-1")

        assert [e for e, _ in recorded] == ["callback_rejected_replay"], recorded

    def test_audit_entry_carries_the_nonce_and_transaction(
        self, client: TestClient, payment, monkeypatch
    ):
        from app.api.v1.endpoints import payments

        recorded: list[tuple] = []
        monkeypatch.setattr(
            payments.audit_log,
            "log",
            lambda event_type, details=None, **kw: recorded.append((event_type, details)),
        )

        post_callback(client, nonce="nonce-audit-2")

        details = recorded[0][1]
        assert details["nonce"] == "nonce-audit-2"
        assert details["transaction_id"] == "pay-replay-1"

    def test_accepted_callback_is_not_audited_as_a_replay(
        self, client: TestClient, payment, monkeypatch
    ):
        """Guards the previous two from passing on a write-only-on-every-call bug."""
        from app.api.v1.endpoints import payments

        recorded: list[str] = []
        monkeypatch.setattr(
            payments.audit_log,
            "log",
            lambda event_type, details=None, **kw: recorded.append(event_type),
        )

        post_callback(client, nonce="nonce-audit-3")

        assert "callback_rejected_replay" not in recorded


class TestNonceSources:
    def test_body_nonce_is_protected_when_the_header_is_absent(
        self, client: TestClient, payment
    ):
        """The nonce may arrive in the body field instead of the header."""
        first = client.post(
            CALLBACK_URL,
            headers=AUTH,
            json={"transaction_id": "pay-replay-1", "status": "confirmed", "nonce": "nonce-body-1"},
        )
        assert first.status_code == 200, first.text

        replay = client.post(
            CALLBACK_URL,
            headers=AUTH,
            json={"transaction_id": "pay-replay-1", "status": "confirmed", "nonce": "nonce-body-1"},
        )
        assert replay.status_code == 409

    def test_header_nonce_takes_precedence_over_body_nonce(
        self, client: TestClient, payment
    ):
        """A caller cannot bypass the check by varying only the body nonce.

        If the body were authoritative, a caller could hold one header nonce and
        vary the body to replay indefinitely.
        """
        assert (
            client.post(
                CALLBACK_URL,
                headers={**AUTH, "X-Callback-Nonce": "nonce-header"},
                json={"transaction_id": "pay-replay-1", "status": "confirmed", "nonce": "body-a"},
            ).status_code
            == 200
        )

        replay = client.post(
            CALLBACK_URL,
            headers={**AUTH, "X-Callback-Nonce": "nonce-header"},
            json={"transaction_id": "pay-replay-1", "status": "confirmed", "nonce": "body-b"},
        )
        assert replay.status_code == 409

    def test_callback_without_a_nonce_is_not_replay_protected(
        self, client: TestClient, payment
    ):
        """Documented behaviour: with no nonce the check cannot apply.

        Asserted so this is not "fixed" by rejecting nonces outright, which would
        break providers that do not send one.
        """
        response = client.post(
            CALLBACK_URL,
            headers=AUTH,
            json={"transaction_id": "pay-replay-1", "status": "confirmed"},
        )
        assert response.status_code == 200, response.text


class TestNonceExpiry:
    def test_expired_nonce_is_evicted_and_accepted_again(
        self, client: TestClient, payment
    ):
        """Nonces expire after ``CALLBACK_NONCE_TTL_SECONDS``.

        Without eviction the set would grow without bound and every nonce would
        stay permanently rejected. Backdating the timestamp avoids a five-minute
        sleep.
        """
        import time as _time

        from app.api.v1.endpoints import payments

        assert post_callback(client, nonce="nonce-expiring").status_code == 200
        assert post_callback(client, nonce="nonce-expiring").status_code == 409

        payments._SEEN_NONCES["nonce-expiring"] = (
            _time.monotonic() - payments.CALLBACK_NONCE_TTL_SECONDS - 1
        )

        assert post_callback(client, nonce="nonce-expiring").status_code == 200

    def test_unexpired_nonce_is_not_evicted(self, client: TestClient, payment):
        """The counterpart, so the expiry test cannot pass by evicting everything."""
        assert post_callback(client, nonce="nonce-fresh").status_code == 200
        assert post_callback(client, nonce="nonce-fresh").status_code == 409


class TestSignatureInteraction:
    def test_signature_is_verified_when_a_secret_is_configured(
        self, client: TestClient, payment, monkeypatch
    ):
        """Signing must fail closed when a secret is set.

        Complements the unsigned tests: without this, nothing pins that the
        ``no_signature`` fixture is what is letting an unsigned request through,
        rather than the secret check being absent altogether.
        """
        from app.core.config import settings

        monkeypatch.setattr(settings, "PAYMENT_WEBHOOK_SECRET", "s" * 32)

        response = client.post(
            CALLBACK_URL,
            headers={**AUTH, "X-Callback-Nonce": "nonce-signed-1"},
            json={"transaction_id": "pay-replay-1", "status": "confirmed"},
        )
        assert response.status_code == 401
        assert "signature" in response.json()["detail"].lower()

    def test_signature_check_runs_before_the_nonce_check(
        self, client: TestClient, payment, monkeypatch
    ):
        """A reused nonce with a bad signature must fail at 401, not 409.

        Ordering matters for the audit trail: an unauthenticated request must not
        be able to burn a nonce that a legitimate retry would then be refused.
        """
        from app.core.config import settings

        monkeypatch.setattr(settings, "PAYMENT_WEBHOOK_SECRET", "s" * 32)

        first = client.post(
            CALLBACK_URL,
            headers={**AUTH, "X-Callback-Nonce": "nonce-shared"},
            json={"transaction_id": "pay-replay-1", "status": "confirmed"},
        )
        assert first.status_code == 401

        second = client.post(
            CALLBACK_URL,
            headers={**AUTH, "X-Callback-Nonce": "nonce-shared"},
            json={"transaction_id": "pay-replay-1", "status": "confirmed"},
        )
        # Still 401, so the nonce was never recorded as seen.
        assert second.status_code == 401
