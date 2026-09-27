"""#357 – Tests for contract request canonicalization and signature stability."""

import pytest

from app.services.contracts.canonicalization import (
    CanonicalRequestBuilder,
    _canonicalize,
    _normalize_numeric,
    _remove_nulls_and_empty,
)


class TestNormalizeNumeric:
    def test_int_unchanged(self):
        assert _normalize_numeric(42) == 42

    def test_float_strips_trailing_zeros(self):
        result = _normalize_numeric(1.100)
        assert result == "1.1"

    def test_float_integer_value(self):
        result = _normalize_numeric(100.0)
        assert result == "100"

    def test_string_number_normalized(self):
        result = _normalize_numeric("1.0000")
        assert result == "1"

    def test_string_non_number_unchanged(self):
        assert _normalize_numeric("hello") == "hello"

    def test_bool_unchanged(self):
        assert _normalize_numeric(True) is True


class TestRemoveNullsAndEmpty:
    def test_removes_none(self):
        assert _remove_nulls_and_empty({"a": 1, "b": None}) == {"a": 1}

    def test_removes_empty_string(self):
        assert _remove_nulls_and_empty({"a": "x", "b": ""}) == {"a": "x"}

    def test_removes_empty_list(self):
        assert _remove_nulls_and_empty({"a": [1], "b": []}) == {"a": [1]}

    def test_removes_empty_dict(self):
        assert _remove_nulls_and_empty({"a": {"k": 1}, "b": {}}) == {"a": {"k": 1}}

    def test_nested(self):
        result = _remove_nulls_and_empty({"a": {"b": None, "c": 1}})
        assert result == {"a": {"c": 1}}


class TestCanonicalize:
    def test_sorted_keys(self):
        result = _canonicalize({"b": 2, "a": 1})
        assert list(result.keys()) == ["a", "b"]

    def test_nested_sorted(self):
        result = _canonicalize({"z": {"c": 3, "a": 1}})
        assert list(result["z"].keys()) == ["a", "c"]

    def test_nulls_stripped(self):
        result = _canonicalize({"a": 1, "b": None, "c": ""})
        assert result == {"a": 1}


class TestCanonicalRequestBuilder:
    def test_deterministic_hash(self):
        a = CanonicalRequestBuilder(salt="test")
        b = CanonicalRequestBuilder(salt="test")
        params = {"amount": 100.00, "to": "GABC...", "memo": "payout"}
        h1 = a.build(params).hash if False else a.build(params) or a.hash()
        h2 = b.build(params).hash if False else b.build(params) or b.hash()
        assert h1 == h2

    def test_different_key_order_same_hash(self):
        a = CanonicalRequestBuilder(salt="s")
        b = CanonicalRequestBuilder(salt="s")
        a.build({"x": 1, "y": 2})
        b.build({"y": 2, "x": 1})
        assert a.hash() == b.hash()

    def test_different_salt_different_hash(self):
        a = CanonicalRequestBuilder(salt="salt1")
        b = CanonicalRequestBuilder(salt="salt2")
        params = {"a": 1}
        a.build(params)
        b.build(params)
        assert a.hash() != b.hash()

    def test_verify_success(self):
        builder = CanonicalRequestBuilder(salt="s")
        params = {"amount": 1.000, "addr": "GABC"}
        builder.build(params)
        h = builder.hash()
        assert builder.verify(params, h) is True

    def test_verify_failure(self):
        builder = CanonicalRequestBuilder(salt="s")
        params = {"amount": 1.000}
        builder.build(params)
        h = builder.hash()
        assert builder.verify({"amount": 2.000}, h) is False

    def test_numeric_normalization_stability(self):
        a = CanonicalRequestBuilder(salt="s")
        b = CanonicalRequestBuilder(salt="s")
        a.build({"amount": "100.00"})
        b.build({"amount": "100.0"})
        assert a.hash() == b.hash()

    def test_null_removal_stability(self):
        a = CanonicalRequestBuilder(salt="s")
        b = CanonicalRequestBuilder(salt="s")
        a.build({"a": 1, "b": None, "c": ""})
        b.build({"a": 1})
        assert a.hash() == b.hash()

    def test_hash_before_build_raises(self):
        builder = CanonicalRequestBuilder()
        with pytest.raises(RuntimeError):
            builder.hash()
