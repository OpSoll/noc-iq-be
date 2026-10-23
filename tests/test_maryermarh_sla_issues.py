"""
Unit tests for Maryermarh's SLA domain issues.

Covers:
- Issue #551: maintenance window time deduction in SLA calculation
- Issue #550: SLA config version hash collision resistance validator
- Issue #549: SLA breach warning threshold notification trigger (80% of threshold)
"""
from app.services.sla.sla_calculator import (
    SLACalculator,
    compute_config_version_hash,
    deduct_maintenance_window,
    sla_warning_threshold_reached,
)


# --------------------------------------------------------------------------- #
# Issue #551: Maintenance window time deduction
# --------------------------------------------------------------------------- #

def test_deduct_maintenance_window_simple():
    adjusted, deducted = deduct_maintenance_window(45, 20)
    assert adjusted == 25
    assert deducted == 20


def test_deduct_maintenance_window_exceeds_mttr_never_negative():
    adjusted, deducted = deduct_maintenance_window(10, 20)
    assert adjusted == 0
    assert deducted == 10


def test_deduct_maintenance_window_no_maintenance():
    adjusted, deducted = deduct_maintenance_window(45, 0)
    assert adjusted == 45
    assert deducted == 0


def test_calculate_sla_applies_maintenance_deduction():
    result = SLACalculator.calculate(
        outage_id="out-1",
        severity="high",
        mttr_minutes=45,
        maintenance_minutes=20,
    )
    # high threshold = 30; effective 25 <= 30 -> met (reward)
    assert result.status == "met"
    assert result.mttr_minutes == 25
    assert result.deducted_maintenance_minutes == 20


def test_calculate_sla_default_no_maintenance_deduction():
    result = SLACalculator.calculate(
        outage_id="out-1",
        severity="high",
        mttr_minutes=45,
    )
    assert result.deducted_maintenance_minutes == 0
    assert result.mttr_minutes == 45
    assert result.status == "violated"


def test_calculate_sla_maintenance_changes_violation_outcome():
    # Without maintenance: 45 > 30 -> violated
    without = SLACalculator.calculate(outage_id="out-1", severity="high", mttr_minutes=45)
    assert without.status == "violated"
    # With maintenance: effective 25 <= 30 -> met
    with_maintenance = SLACalculator.calculate(
        outage_id="out-1", severity="high", mttr_minutes=45, maintenance_minutes=20
    )
    assert with_maintenance.status == "met"


# --------------------------------------------------------------------------- #
# Issue #550: Config version hash collision resistance
# --------------------------------------------------------------------------- #

def test_config_hash_deterministic_same_config():
    a = SLACalculator.calculate(outage_id="out-1", severity="critical", mttr_minutes=5)
    b = SLACalculator.calculate(outage_id="out-2", severity="critical", mttr_minutes=8)
    assert a.config_version_hash == b.config_version_hash
    assert isinstance(a.config_version_hash, str)
    assert len(a.config_version_hash) == 64


def test_config_hash_differs_across_severities():
    critical = SLACalculator.calculate(outage_id="out-1", severity="critical", mttr_minutes=5)
    low = SLACalculator.calculate(outage_id="out-2", severity="low", mttr_minutes=5)
    assert critical.config_version_hash != low.config_version_hash


def test_compute_config_version_hash_helpers():
    from app.services.sla.config import get_config_for_severity

    critical = compute_config_version_hash(get_config_for_severity("critical"))
    high = compute_config_version_hash(get_config_for_severity("high"))
    critical_again = compute_config_version_hash(get_config_for_severity("critical"))

    assert critical == critical_again
    assert critical != high


def test_config_hash_is_sha256_hex():
    from app.services.sla.config import get_config_for_severity

    digest = compute_config_version_hash(get_config_for_severity("low"))
    assert len(digest) == 64
    int(digest, 16)  # ensure it is valid hex


# --------------------------------------------------------------------------- #
# Issue #549: SLA breach warning threshold (80% of threshold)
# --------------------------------------------------------------------------- #

def test_warning_exact_80_percent_reached():
    # threshold 30 -> 80% = 24; exactly 24 should warn
    assert sla_warning_threshold_reached(24, 30) is True


def test_warning_below_80_percent_not_reached():
    # threshold 30 -> 80% = 24; 23 below -> no warning
    assert sla_warning_threshold_reached(23, 30) is False


def test_warning_just_above_80_percent_reached():
    assert sla_warning_threshold_reached(25, 30) is True


def test_warning_at_threshold_not_warning():
    # At/above threshold means breached (violation), not a warning
    assert sla_warning_threshold_reached(30, 30) is False
    assert sla_warning_threshold_reached(31, 30) is False


def test_warning_custom_fraction_boundary():
    # 80% of 60 = 48
    assert sla_warning_threshold_reached(48, 60, warning_fraction=0.8) is True
    assert sla_warning_threshold_reached(47, 60, warning_fraction=0.8) is False


def test_warning_zero_threshold_never_fires():
    assert sla_warning_threshold_reached(0, 0) is False
    assert sla_warning_threshold_reached(10, 0) is False
