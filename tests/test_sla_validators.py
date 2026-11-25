import pytest

from app.services.sla.sla_validators import (
    KNOWN_TIERS,
    evaluate_threshold,
    map_result_fields,
    serialize_tier,
    validate_bulk_config,
)

FIELDS = ["outage_id", "status", "mttr_minutes", "threshold_minutes", "amount"]


def test_every_field_maps():
    result = {f: i for i, f in enumerate(FIELDS)}
    result["future_field"] = 1
    assert list(map_result_fields(result, FIELDS)) == FIELDS
    with pytest.raises(ValueError):
        map_result_fields(result, FIELDS, allow_unknown=False)
    with pytest.raises(KeyError):
        map_result_fields({"status": 1}, FIELDS)


@pytest.mark.parametrize("severity,threshold", [("critical", 15), ("high", 30), ("medium", 60), ("low", 120)])
def test_threshold_equality(severity, threshold):
    assert evaluate_threshold(threshold, threshold) == (True, "within_threshold", "met")
    assert evaluate_threshold(threshold + 1, threshold) == (False, "threshold_exceeded", "violated")


def test_duplicate_severity_rejected_and_unchanged():
    existing = {"high": {"severity": "high", "threshold": 30}}
    rows = [{"severity": "high", "threshold": 10}, {"severity": "HIGH", "threshold": 20}]
    with pytest.raises(ValueError):
        validate_bulk_config(rows, existing)
    assert existing["high"]["threshold"] == 30


def test_tier_serialization():
    for tier in KNOWN_TIERS:
        assert serialize_tier(tier.upper()) == tier
    with pytest.raises(ValueError):
        serialize_tier("diamond")
