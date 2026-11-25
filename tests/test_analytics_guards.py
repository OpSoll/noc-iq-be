from datetime import timedelta, timezone

import pytest

from app.services.analytics_guards import (
    EXPORT_COLUMNS,
    export_header,
    fill_buckets,
    utc,
    validate_rate_keys,
    validate_sort,
)


def test_rate_keys():
    assert validate_rate_keys({"XLM": "0.1", "USDC": "1"})
    with pytest.raises(ValueError, match="usd c"):
        validate_rate_keys({"usd c": "1"})


def test_sparse_buckets_zero_filled_and_tz_independent():
    lagos = timezone(timedelta(hours=1))
    points = [(utc(2026, 1, 1), 3), (utc(2026, 1, 4).astimezone(lagos), 5)]
    buckets = fill_buckets(points, utc(2026, 1, 1), utc(2026, 1, 4))
    assert [v for _, v in buckets] == [3, 0, 0, 5]
    assert [b for b, _ in buckets] == sorted(b for b, _ in buckets)


def test_empty_export_keeps_columns():
    empty = export_header([])
    full = export_header([{"period": "2026-01", "total": 1}])
    assert empty["columns"] == full["columns"] == EXPORT_COLUMNS
    assert empty["rows"] == []


def test_sort_guard():
    assert validate_sort("-total") == ("-total", "id")
    with pytest.raises(ValueError):
        validate_sort("id; DROP TABLE outages")
