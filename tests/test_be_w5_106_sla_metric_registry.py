"""Unit tests for SLA dashboard metric definition registry (BE-W5-106 / issue #367).

Verifies:
- Registry completeness and structural correctness
- Formula correctness across representative datasets
- Edge-case handling (zero divisors, boundary values)
- compute_metric() helper round-trip
"""
import pytest

from app.services.sla_metric_registry import (
    METRIC_REGISTRY,
    MetricDefinition,
    compute_metric,
    get_metric,
    list_metrics,
)


# ---------------------------------------------------------------------------
# Registry structure
# ---------------------------------------------------------------------------

class TestRegistryStructure:
    """Registry must be complete and internally consistent."""

    REQUIRED_METRICS = {
        "violation_rate",
        "net_payout",
        "avg_mttr",
        "availability",
        "penalty_exposure",
        "reward_per_met",
    }

    def test_all_required_metrics_are_registered(self):
        assert self.REQUIRED_METRICS.issubset(METRIC_REGISTRY.keys())

    def test_every_metric_is_a_metric_definition(self):
        for name, defn in METRIC_REGISTRY.items():
            assert isinstance(defn, MetricDefinition), f"{name} is not a MetricDefinition"

    def test_every_metric_has_non_empty_description(self):
        for name, defn in METRIC_REGISTRY.items():
            assert defn.description.strip(), f"{name} has empty description"

    def test_every_metric_has_at_least_one_input(self):
        for name, defn in METRIC_REGISTRY.items():
            assert len(defn.inputs) >= 1, f"{name} has no declared inputs"

    def test_every_metric_has_callable_formula(self):
        for name, defn in METRIC_REGISTRY.items():
            assert callable(defn.formula), f"{name} formula is not callable"

    def test_list_metrics_returns_all_sorted(self):
        metrics = list_metrics()
        names = [m.name for m in metrics]
        assert sorted(names) == names
        assert set(names) == set(METRIC_REGISTRY.keys())

    def test_get_metric_returns_correct_definition(self):
        defn = get_metric("violation_rate")
        assert defn.name == "violation_rate"

    def test_get_metric_raises_for_unknown_name(self):
        with pytest.raises(KeyError):
            get_metric("nonexistent_metric_xyz")


# ---------------------------------------------------------------------------
# violation_rate formula
# ---------------------------------------------------------------------------

class TestViolationRateFormula:
    def test_no_violations(self):
        assert compute_metric("violation_rate", total_outages=10, total_violations=0) == 0.0

    def test_all_violated(self):
        assert compute_metric("violation_rate", total_outages=5, total_violations=5) == 1.0

    def test_partial_violation(self):
        result = compute_metric("violation_rate", total_outages=4, total_violations=1)
        assert result == pytest.approx(0.25)

    def test_zero_outages_returns_zero(self):
        assert compute_metric("violation_rate", total_outages=0, total_violations=0) == 0.0

    def test_single_outage_violated(self):
        assert compute_metric("violation_rate", total_outages=1, total_violations=1) == 1.0

    def test_representative_dataset_50_percent(self):
        assert compute_metric("violation_rate", total_outages=100, total_violations=50) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# net_payout formula
# ---------------------------------------------------------------------------

class TestNetPayoutFormula:
    def test_rewards_only_is_positive(self):
        assert compute_metric("net_payout", total_rewards=500.0, total_penalties=0.0) == pytest.approx(500.0)

    def test_penalties_only_is_negative(self):
        assert compute_metric("net_payout", total_rewards=0.0, total_penalties=300.0) == pytest.approx(-300.0)

    def test_balanced(self):
        assert compute_metric("net_payout", total_rewards=200.0, total_penalties=200.0) == pytest.approx(0.0)

    def test_rewards_exceed_penalties(self):
        result = compute_metric("net_payout", total_rewards=1000.0, total_penalties=400.0)
        assert result == pytest.approx(600.0)

    def test_zero_both(self):
        assert compute_metric("net_payout", total_rewards=0.0, total_penalties=0.0) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# avg_mttr formula
# ---------------------------------------------------------------------------

class TestAvgMttrFormula:
    def test_no_resolved_outages_returns_zero(self):
        assert compute_metric("avg_mttr", total_mttr_minutes=0.0, total_resolved=0) == 0.0

    def test_single_outage(self):
        assert compute_metric("avg_mttr", total_mttr_minutes=45.0, total_resolved=1) == pytest.approx(45.0)

    def test_multiple_outages(self):
        result = compute_metric("avg_mttr", total_mttr_minutes=180.0, total_resolved=3)
        assert result == pytest.approx(60.0)

    def test_fractional_mttr(self):
        result = compute_metric("avg_mttr", total_mttr_minutes=100.0, total_resolved=3)
        assert result == pytest.approx(100.0 / 3)


# ---------------------------------------------------------------------------
# availability formula
# ---------------------------------------------------------------------------

class TestAvailabilityFormula:
    def test_no_downtime_is_100(self):
        assert compute_metric("availability", downtime_minutes=0.0, period_minutes=10080.0) == pytest.approx(100.0)

    def test_total_downtime_is_zero(self):
        assert compute_metric("availability", downtime_minutes=10080.0, period_minutes=10080.0) == pytest.approx(0.0)

    def test_99_9_percent(self):
        period = 10080.0  # 7 days in minutes
        downtime = period * 0.001  # 0.1% downtime
        result = compute_metric("availability", downtime_minutes=downtime, period_minutes=period)
        assert result == pytest.approx(99.9, abs=1e-6)

    def test_clamped_at_100(self):
        """Negative downtime (data error) must not exceed 100%."""
        result = compute_metric("availability", downtime_minutes=-5.0, period_minutes=100.0)
        assert result == pytest.approx(100.0)

    def test_clamped_at_zero(self):
        """Downtime exceeding period must not go below 0%."""
        result = compute_metric("availability", downtime_minutes=200.0, period_minutes=100.0)
        assert result == pytest.approx(0.0)

    def test_zero_period_returns_100(self):
        """Undefined period defaults to 100% (safe fallback)."""
        assert compute_metric("availability", downtime_minutes=0.0, period_minutes=0.0) == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# penalty_exposure formula
# ---------------------------------------------------------------------------

class TestPenaltyExposureFormula:
    def test_no_outages_returns_zero(self):
        assert compute_metric("penalty_exposure", total_penalties=0.0, total_outages=0) == 0.0

    def test_single_outage(self):
        assert compute_metric("penalty_exposure", total_penalties=500.0, total_outages=1) == pytest.approx(500.0)

    def test_distributed_penalty(self):
        result = compute_metric("penalty_exposure", total_penalties=300.0, total_outages=6)
        assert result == pytest.approx(50.0)

    def test_no_penalties_no_exposure(self):
        assert compute_metric("penalty_exposure", total_penalties=0.0, total_outages=10) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# reward_per_met formula
# ---------------------------------------------------------------------------

class TestRewardPerMetFormula:
    def test_no_met_returns_zero(self):
        assert compute_metric("reward_per_met", total_rewards=0.0, met_count=0) == 0.0

    def test_single_met(self):
        assert compute_metric("reward_per_met", total_rewards=750.0, met_count=1) == pytest.approx(750.0)

    def test_multiple_met(self):
        result = compute_metric("reward_per_met", total_rewards=3000.0, met_count=4)
        assert result == pytest.approx(750.0)


# ---------------------------------------------------------------------------
# compute_metric helper
# ---------------------------------------------------------------------------

class TestComputeMetricHelper:
    def test_unknown_metric_raises_key_error(self):
        with pytest.raises(KeyError, match="Unknown metric"):
            compute_metric("does_not_exist", total_outages=5)

    def test_missing_input_raises_type_error(self):
        with pytest.raises(TypeError, match="Missing inputs"):
            compute_metric("violation_rate", total_outages=10)  # total_violations missing

    def test_extra_kwargs_are_ignored(self):
        """Extra keyword arguments must not break computation."""
        result = compute_metric(
            "violation_rate",
            total_outages=10,
            total_violations=3,
            irrelevant_key="ignored",
        )
        assert result == pytest.approx(0.3)
