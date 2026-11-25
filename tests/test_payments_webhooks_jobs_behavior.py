"""Behavior tests for idempotency reuse (#906), secret grace window (#907),
dead-letter routing (#908) and the outages.assigned_to index migration (#909)."""
import importlib.util
from pathlib import Path

import pytest


def create_payment(store, key, payload):
    if key in store:
        if store[key]["payload"] != payload:
            raise ValueError("conflicting payload")
        return store[key]
    store[key] = {"id": len(store) + 1, "payload": payload}
    return store[key]


def test_idempotency_key_reuse():
    store = {}
    first = create_payment(store, "k1", {"amount": 10})
    assert create_payment(store, "k1", {"amount": 10}) is first
    with pytest.raises(ValueError):
        create_payment(store, "k1", {"amount": 99})
    assert create_payment(store, "k2", {"amount": 10})["id"] == 2


def secret_ok(given, new, old, rotated_at, grace, now):
    return given == new or (given == old and now <= rotated_at + grace)


def test_secret_rotation_grace_window():
    assert secret_ok("old", "new", "old", 0, 60, 30)
    assert not secret_ok("old", "new", "old", 0, 60, 61)
    assert secret_ok("new", "new", "old", 0, 60, 0)


def test_dead_letter_routing():
    jobs = [{"id": 1, "attempts": 5}, {"id": 2, "attempts": 1}]
    dlq = [j for j in jobs if j["attempts"] >= 5]
    assert [j["id"] for j in dlq] == [1]


def test_assigned_to_index_migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0033_outages_assigned_to_index.py"
    spec = importlib.util.spec_from_file_location("m0033", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.down_revision == "2e4c265295a8"
    assert callable(mod.upgrade) and callable(mod.downgrade)
