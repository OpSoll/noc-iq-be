import threading
from datetime import datetime, timedelta, timezone

import pytest

from app.services.outage_payment_guards import (
    DuplicateTransaction,
    TxRegistry,
    VersionConflict,
    apply_update,
    check_callback_network,
    duration_minutes,
)


def test_stale_version_conflict():
    rec = {"version": 1, "status": "open"}
    apply_update(rec, 1, {"status": "resolved"})
    with pytest.raises(VersionConflict):
        apply_update(rec, 1, {"status": "open"})
    assert rec == {"version": 2, "status": "resolved"}


def test_dst_durations_use_utc():
    before = timezone(timedelta(hours=-5))
    after = timezone(timedelta(hours=-4))
    start = datetime(2026, 3, 8, 1, 0, tzinfo=before)
    end = datetime(2026, 3, 8, 3, 0, tzinfo=after)
    assert duration_minutes(start, end) == 60
    fall_start = datetime(2026, 11, 1, 1, 0, tzinfo=after)
    fall_end = datetime(2026, 11, 1, 1, 0, tzinfo=before)
    assert duration_minutes(fall_start, fall_end) == 60


def test_tx_hash_race_one_row():
    reg, outcomes = TxRegistry(), []

    def worker():
        try:
            reg.insert("abc", {"payout": 1})
            outcomes.append("ok")
        except DuplicateTransaction:
            outcomes.append("dup")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(outcomes) == ["dup", "ok"]


def test_wrong_network_rejected():
    audit = []
    assert not check_callback_network("testnet", "mainnet", audit)
    assert audit == [{"event": "callback_rejected", "reason": "network_mismatch"}]
    assert check_callback_network("testnet", "testnet", [])
