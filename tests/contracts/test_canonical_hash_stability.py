"""
Issue #954 – Canonical request hashing stability.

Canonicalization underpins idempotency, so the hash produced by
``CanonicalRequestBuilder`` must be:
- stable across key orderings (top-level and nested),
- stable across JSON whitespace / formatting of the incoming body,
- different for semantically different payloads,
- pinned to a known digest so algorithm drift is caught.
"""

import json
import random

import pytest

from app.services.contracts.canonicalization import CanonicalRequestBuilder

SALT = "test-salt"

BASE_PAYLOAD = {
    "contract_id": "CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC",
    "method": "calculate_sla",
    "args": {
        "outage_id": "out-123",
        "severity": "critical",
        "mttr_minutes": 42,
        "penalty": {"amount": "150.50", "asset": "USDC"},
    },
    "tags": ["sla", "penalty"],
}


def _hash(payload, salt=SALT):
    builder = CanonicalRequestBuilder(salt=salt)
    builder.build(payload)
    return builder.hash()


def _reorder(obj, rng):
    """Return a deep copy of *obj* with every dict's keys shuffled."""
    if isinstance(obj, dict):
        items = list(obj.items())
        rng.shuffle(items)
        return {k: _reorder(v, rng) for k, v in items}
    if isinstance(obj, list):
        return [_reorder(v, rng) for v in obj]
    return obj


class TestStableAcrossKeyOrder:
    def test_top_level_reversed(self):
        reversed_payload = dict(reversed(list(BASE_PAYLOAD.items())))
        assert list(reversed_payload) != list(BASE_PAYLOAD)
        assert _hash(reversed_payload) == _hash(BASE_PAYLOAD)

    def test_nested_reordered(self):
        payload = dict(BASE_PAYLOAD)
        payload["args"] = {
            "penalty": {"asset": "USDC", "amount": "150.50"},
            "mttr_minutes": 42,
            "severity": "critical",
            "outage_id": "out-123",
        }
        assert _hash(payload) == _hash(BASE_PAYLOAD)

    @pytest.mark.parametrize("seed", range(20))
    def test_random_deep_shuffles(self, seed):
        shuffled = _reorder(BASE_PAYLOAD, random.Random(seed))
        assert _hash(shuffled) == _hash(BASE_PAYLOAD)

    def test_canonical_form_has_sorted_keys(self):
        builder = CanonicalRequestBuilder(salt=SALT)
        canonical = builder.build(_reorder(BASE_PAYLOAD, random.Random(7)))
        assert list(canonical) == sorted(canonical)
        assert list(canonical["args"]) == sorted(canonical["args"])

    def test_repeated_builds_are_deterministic(self):
        builder = CanonicalRequestBuilder(salt=SALT)
        hashes = set()
        for _ in range(5):
            builder.build(BASE_PAYLOAD)
            hashes.add(builder.hash())
        assert len(hashes) == 1

    def test_input_is_not_mutated(self):
        snapshot = json.dumps(BASE_PAYLOAD)
        _hash(BASE_PAYLOAD)
        assert json.dumps(BASE_PAYLOAD) == snapshot


class TestStableAcrossWhitespace:
    """Request bodies arriving with different JSON formatting hash identically."""

    @pytest.mark.parametrize(
        "dump_kwargs",
        [
            {"separators": (",", ":")},
            {"indent": 2},
            {"indent": 4, "sort_keys": True},
            {"separators": (" , ", " : ")},
        ],
    )
    def test_formatting_variants(self, dump_kwargs):
        body = json.dumps(BASE_PAYLOAD, **dump_kwargs)
        assert _hash(json.loads(body)) == _hash(BASE_PAYLOAD)

    def test_tabs_and_newlines_between_tokens(self):
        body = '{\n\t"method" :\t"calculate_sla",\r\n  "args": { "outage_id":"out-1" }\n}\n'
        compact = '{"args":{"outage_id":"out-1"},"method":"calculate_sla"}'
        assert _hash(json.loads(body)) == _hash(json.loads(compact))

    def test_whitespace_inside_string_values_is_significant(self):
        # Formatting whitespace is ignored, but whitespace in values is data.
        assert _hash({"memo": "a b"}) != _hash({"memo": "ab"})


class TestDifferentPayloadsDiffer:
    def _variant(self, **changes):
        payload = json.loads(json.dumps(BASE_PAYLOAD))
        for path, value in changes.items():
            target = payload
            *parents, leaf = path.split("__")
            for key in parents:
                target = target[key]
            target[leaf] = value
        return payload

    @pytest.mark.parametrize(
        "changes",
        [
            {"method": "calculate_penalty"},
            {"args__outage_id": "out-124"},
            {"args__severity": "minor"},
            {"args__mttr_minutes": 43},
            {"args__penalty__amount": "150.51"},
            {"args__penalty__asset": "XLM"},
            {"tags": ["penalty", "sla"]},  # list order is significant
            {"extra_field": "x"},
        ],
    )
    def test_changed_value_changes_hash(self, changes):
        assert _hash(self._variant(**changes)) != _hash(BASE_PAYLOAD)

    def test_value_moved_between_keys_changes_hash(self):
        assert _hash({"a": "1", "b": "2"}) != _hash({"a": "2", "b": "1"})

    def test_nesting_level_changes_hash(self):
        assert _hash({"a": {"b": "c"}}) != _hash({"a_b": "c"})

    def test_salt_provides_domain_separation(self):
        assert _hash(BASE_PAYLOAD, salt="one") != _hash(BASE_PAYLOAD, salt="two")

    def test_many_distinct_payloads_have_distinct_hashes(self):
        hashes = {_hash({"outage_id": f"out-{i}", "seq": i}) for i in range(500)}
        assert len(hashes) == 500


class TestHashFormatAndPinning:
    def test_hash_is_sha256_hex(self):
        digest = _hash(BASE_PAYLOAD)
        assert len(digest) == 64
        int(digest, 16)

    def test_known_digest_is_pinned(self):
        # Pins the canonical encoding. If this changes, every stored
        # idempotency key is invalidated — update only with a migration plan.
        assert _hash({"b": "2", "a": {"d": 1, "c": "x"}}) == PINNED_DIGEST


# sha256("test-salt" + '{"a":{"c":"x","d":1},"b":"2"}')
PINNED_DIGEST = "a1ecbd30014cf23dfe887949cb65cfa0a3b45f9b59edb56e8dccda99384631de"
