import threading

import pytest

from app.services.contracts.contract_guards import (
    CircuitGuard,
    ContractConfigError,
    check_key_conflict,
    key_reusable,
    reject_non_finite,
    select_execution_mode,
)


def test_mode_selection_and_errors():
    assert select_execution_mode("local") == "local"
    assert select_execution_mode("contract", "http://bridge") == "contract"
    with pytest.raises(ContractConfigError):
        select_execution_mode("bogus")
    with pytest.raises(ContractConfigError):
        select_execution_mode("contract")


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_rejected(bad):
    with pytest.raises(ValueError):
        reject_non_finite({"a": [1, bad]})
    assert reject_non_finite({"a": 1.5}) == {"a": 1.5}


def test_concurrent_failures_open_once():
    clock = [100.0]
    guard = CircuitGuard(threshold=2, cooldown=10, probes=1, clock=lambda: clock[0])
    threads = [threading.Thread(target=guard.record_failure) for _ in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert guard.opened_at == 100.0 and not guard.allow()
    clock[0] = 111.0
    assert guard.allow() and not guard.allow()


def test_idempotency_boundary():
    assert not key_reusable(0, 60, 60)
    assert key_reusable(0, 60, 61)
    with pytest.raises(ValueError):
        check_key_conflict("a", "b", 0, 60, 30)
    assert check_key_conflict("a", "b", 0, 60, 61)
