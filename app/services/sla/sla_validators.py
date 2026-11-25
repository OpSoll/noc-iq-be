"""Small SLA helpers: schema field mapping, threshold checks, bulk config and tier validation."""

KNOWN_TIERS = ("platinum", "gold", "silver", "bronze")


def map_result_fields(result, schema_fields, allow_unknown=True):
    """Map contract result fields to the public schema (#1035).

    Missing schema fields raise; unknown extra fields are dropped when
    allow_unknown (forward-compatible) else rejected.
    """
    missing = [f for f in schema_fields if f not in result]
    if missing:
        raise KeyError(f"missing schema fields: {missing}")
    extra = [k for k in result if k not in schema_fields]
    if extra and not allow_unknown:
        raise ValueError(f"unknown fields: {extra}")
    return {f: result[f] for f in schema_fields}


def evaluate_threshold(minutes, threshold):
    """Equality counts as met (#1036). Returns (met, reason_code, rating)."""
    if minutes <= threshold:
        return True, "within_threshold", "met"
    return False, "threshold_exceeded", "violated"


def validate_bulk_config(rows, existing):
    """Reject duplicate severities before persistence (#1037)."""
    seen = set()
    for row in rows:
        sev = str(row["severity"]).lower()
        if sev in seen:
            raise ValueError(f"duplicate severity: {sev}")
        seen.add(sev)
    updated = dict(existing)
    updated.update({str(r["severity"]).lower(): r for r in rows})
    return updated


def serialize_tier(value):
    """Unknown persisted tiers raise instead of mapping to a valid rating (#1038)."""
    name = str(value).lower()
    if name not in KNOWN_TIERS:
        raise ValueError(f"unknown SLA tier: {value!r}")
    return name
