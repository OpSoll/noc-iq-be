import pytest

from src.audit.audit_guards import escape_like, filter_export, percentile, prune


def test_percentile_empty_and_singleton():
    assert percentile([], 95) is None
    for pct in (50, 90, 95, 99):
        assert percentile([7], pct) == 7


def test_like_wildcards_are_literal():
    assert escape_like("50%_off") == "%50\\%\\_off%"
    assert escape_like("router") == "%router%"


def test_export_role_and_window():
    rows = [{"timestamp": t} for t in (1, 5, 10)]
    with pytest.raises(PermissionError):
        filter_export(rows, "viewer", 0, 10)
    assert filter_export(rows, "admin", 2, 9) == [{"timestamp": 5}]


def test_prune_boundary_and_idempotent():
    rows = [{"timestamp": t} for t in (9, 10, 11)]
    once = prune(rows, 10)
    assert once == [{"timestamp": 10}, {"timestamp": 11}]
    assert prune(once, 10) == once
