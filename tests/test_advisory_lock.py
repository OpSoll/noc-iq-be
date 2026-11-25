"""Tests for the advisory lock helper and its failure paths (#894)."""

import hashlib

import pytest

from app.core.lock import (
    ConcurrencyLockError,
    _lock_id_from_key,
    advisory_lock,
    advisory_lock_nowait,
)


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class FakeSession:
    """Records executed statements; ``acquired`` controls the lock outcome."""

    def __init__(self, acquired=True, error=None):
        self.acquired = acquired
        self.error = error
        self.executed = []

    def execute(self, statement, params=None):
        self.executed.append(params)
        if self.error is not None:
            raise self.error
        return FakeResult(self.acquired)


def test_lock_key_is_deterministic():
    assert _lock_id_from_key("resolve:outage-1") == _lock_id_from_key("resolve:outage-1")
    assert _lock_id_from_key("resolve:outage-1") != _lock_id_from_key("resolve:outage-2")


def test_lock_key_matches_sha256_prefix():
    expected = int.from_bytes(hashlib.sha256(b"key").digest()[:8], "big")

    assert _lock_id_from_key("key") == expected


def test_successful_acquisition_runs_the_block():
    db = FakeSession(acquired=True)
    ran = False

    with advisory_lock_nowait(db, "resolve:outage-1"):
        ran = True

    assert ran is True
    assert db.executed[0]["lock_id"] == _lock_id_from_key("resolve:outage-1")


def test_contention_raises_concurrency_lock_error():
    db = FakeSession(acquired=False)

    with pytest.raises(ConcurrencyLockError, match="already in progress"):
        with advisory_lock_nowait(db, "resolve:outage-1"):
            pass


def test_advisory_lock_contention_raises_concurrency_lock_error():
    db = FakeSession(acquired=False)

    with pytest.raises(ConcurrencyLockError):
        with advisory_lock(db, "resolve:outage-1"):
            pass


def test_errors_inside_the_block_propagate():
    db = FakeSession(acquired=True)

    with pytest.raises(ValueError, match="boom"):
        with advisory_lock_nowait(db, "resolve:outage-1"):
            raise ValueError("boom")


def test_advisory_lock_nowait_treats_execute_failure_as_acquired():
    """The helper deliberately degrades open if the probe itself errors."""
    db = FakeSession(error=RuntimeError("driver error"))
    ran = False

    with advisory_lock_nowait(db, "resolve:outage-1"):
        ran = True

    assert ran is True
