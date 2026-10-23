"""Unit tests for SLA issues assigned to zainabbaba31-source.

- #545: SLACalculator.resolve_offline() off-chain fallback (is_offline_fallback)
- #546: MTTR bounds validation (0 .. 525,600 minutes)
- #547: Severity penalty multiplier monotonicity validation
- #548: SLA uptime percentage with Decimal precision ("99.9500%")
"""
import pytest

from app.models.sla import SLAResult, SLASeverityConfig
from app.services.sla.config import SLA_CONFIG, get_all_config
from app.services.sla.errors import InvalidMTTRError, InvalidSLAConfigError
from app.services.sla.sla_calculator import (
    MAX_MTTR_MINUTES,
    MIN_MTTR_MINUTES,
    SLACalculator,
    UPTIME_DECIMAL_PLACES,
    compute_uptime_percentage,
    validate_mttr,
)


def _config_from_raw(**overrides):
    """Build a full severity config (plain dicts) with per-severity overrides."""
    raw = {severity: dict(values) for severity, values in SLA_CONFIG.items()}
    for severity, patch in overrides.items():
        raw[severity].update(patch)
    return raw


def _config_models(**overrides):
    """Build a full severity config (SLASeverityConfig objects) with overrides."""
    cfg = get_all_config()
    for severity, patch in overrides.items():
        cfg[severity] = cfg[severity].model_copy(update=patch)
    return cfg


def _without_fallback_flag(result):
    dump = result.model_dump()
    dump.pop("is_offline_fallback")
    return dump


# ---------------------------------------------------------------------------
# #546 – MTTR bounds validation
# ---------------------------------------------------------------------------

class TestMTTRValidation:
    """mttr_minutes must stay within [0, 525600] (#546)."""

    def test_constants(self):
        assert MIN_MTTR_MINUTES == 0
        assert MAX_MTTR_MINUTES == 525600

    def test_validate_mttr_returns_input(self):
        assert validate_mttr(120) == 120

    def test_zero_is_valid(self):
        validate_mttr(0)

    def test_max_boundary_is_valid(self):
        validate_mttr(MAX_MTTR_MINUTES)

    def test_negative_raises(self):
        with pytest.raises(InvalidMTTRError):
            validate_mttr(-1)

    def test_above_max_raises(self):
        with pytest.raises(InvalidMTTRError):
            validate_mttr(MAX_MTTR_MINUTES + 1)

    def test_one_hundred_years_raises(self):
        with pytest.raises(InvalidMTTRError):
            validate_mttr(52560000)

    def test_non_numeric_raises(self):
        with pytest.raises(InvalidMTTRError):
            validate_mttr("soon")

    def test_invalid_mttr_is_value_error(self):
        assert issubclass(InvalidMTTRError, ValueError)

    def test_calculate_sla_rejects_negative(self):
        with pytest.raises(InvalidMTTRError):
            SLACalculator.calculate_sla("outage-1", "low", -1)

    def test_calculate_within_bounds_succeeds(self):
        result = SLACalculator.calculate_sla("outage-1", "low", MAX_MTTR_MINUTES)
        assert isinstance(result, SLAResult)


# ---------------------------------------------------------------------------
# #547 – Penalty multiplier monotonicity validation
# ---------------------------------------------------------------------------

class TestValidateConfig:
    """SLACalculator.validate_config must enforce critical >= high >= medium >= low (#547)."""

    def test_default_config_validates(self):
        config = SLACalculator.validate_config()
        assert set(config.keys()) == {"critical", "high", "medium", "low"}

    def test_ordered_penalties_valid(self):
        config = _config_models(critical={"penalty_per_minute": 100}, high={"penalty_per_minute": 50})
        SLACalculator.validate_config(config)

    def test_equal_penalties_are_valid(self):
        config = _config_from_raw(
            critical={"penalty_per_minute": 50},
            high={"penalty_per_minute": 50},
            medium={"penalty_per_minute": 50},
            low={"penalty_per_minute": 50},
        )
        SLACalculator.validate_config(config)

    def test_inverted_penalties_raise(self):
        config = _config_from_raw(medium={"penalty_per_minute": 60})
        with pytest.raises(InvalidSLAConfigError):
            SLACalculator.validate_config(config)

    def test_inverted_penalties_models_raise(self):
        config = _config_models(high={"penalty_per_minute": 10}, low={"penalty_per_minute": 50})
        with pytest.raises(InvalidSLAConfigError):
            SLACalculator.validate_config(config)

    def test_missing_severity_raises(self):
        config = _config_from_raw()
        del config["low"]
        with pytest.raises(InvalidSLAConfigError):
            SLACalculator.validate_config(config)

    def test_raw_dict_config_is_normalized(self):
        config = SLACalculator.validate_config(_config_from_raw())
        for severity in ("critical", "high", "medium", "low"):
            assert isinstance(config[severity], SLASeverityConfig)
        assert [c.penalty_per_minute for c in config.values()] == sorted(
            [c.penalty_per_minute for c in config.values()], reverse=True
        )

    def test_invalid_config_error_is_value_error(self):
        assert issubclass(InvalidSLAConfigError, ValueError)


# ---------------------------------------------------------------------------
# #548 – Uptime percentage with Decimal precision
# ---------------------------------------------------------------------------

class TestComputeUptimePercentage:
    """compute_uptime_percentage must format to 4 decimals, e.g. "99.9500%" (#548)."""

    def test_four_decimal_places(self):
        assert compute_uptime_percentage(9995, 10000) == "99.9500%"

    def test_plain_percentage(self):
        assert compute_uptime_percentage(9500, 10000) == "95.0000%"
        assert compute_uptime_percentage(1000, 10000) == "10.0000%"

    def test_zero_outages_returns_full_uptime(self):
        assert compute_uptime_percentage(0, 0) == "100.0000%"

    def test_full_availability(self):
        assert compute_uptime_percentage(100, 100) == "100.0000%"

    def test_full_downtime(self):
        assert compute_uptime_percentage(0, 100) == "0.0000%"

    def test_half_up_rounding(self):
        assert compute_uptime_percentage(2, 3) == "66.6667%"
        assert compute_uptime_percentage(1, 3) == "33.3333%"

    def test_repeating_decimals_kept_bounded(self):
        assert compute_uptime_percentage(1, 6) == "16.6667%"

    def test_available_clamped_to_total(self):
        assert compute_uptime_percentage(150, 100) == "100.0000%"

    def test_output_is_string_with_percent_and_four_decimals(self):
        for available, total in ((9995, 10000), (0, 100), (100, 100), (1, 3), (0, 0)):
            value = compute_uptime_percentage(available, total)
            assert isinstance(value, str)
            assert value.endswith("%")
            decimals = value[:-1].split(".")[1]
            assert len(decimals) == UPTIME_DECIMAL_PLACES


# ---------------------------------------------------------------------------
# #545 – resolve_offline() fallback calculation
# ---------------------------------------------------------------------------

class TestResolveOffline:
    """resolve_offline must mirror online math and tag is_offline_fallback=True (#545)."""

    def test_offline_flag_is_set(self):
        result = SLACalculator.resolve_offline("outage-1", "high", 20)
        assert isinstance(result, SLAResult)
        assert result.is_offline_fallback is True

    def test_online_path_defaults_flag_to_false(self):
        assert SLACalculator.calculate("outage-1", "high", 20).is_offline_fallback is False
        assert SLACalculator.calculate_sla("outage-1", "high", 20).is_offline_fallback is False

    def test_offline_math_matches_online_met(self):
        online = SLACalculator.calculate("outage-1", "medium", 30)
        offline = SLACalculator.resolve_offline("outage-1", "medium", 30)
        assert offline.status == "met"
        assert _without_fallback_flag(offline) == _without_fallback_flag(online)

    def test_offline_math_matches_online_violated(self):
        online = SLACalculator.calculate("outage-1", "critical", 30)
        offline = SLACalculator.resolve_offline("outage-1", "critical", 30)
        assert offline.status == "violated"
        assert _without_fallback_flag(offline) == _without_fallback_flag(online)

    def test_offline_penalty_amount_math(self):
        offline = SLACalculator.resolve_offline("outage-1", "critical", 30)
        assert offline.threshold_minutes == 15
        assert offline.amount == -(15 * 100)
        assert offline.payment_type == "penalty"

    def test_offline_reward_amount_math(self):
        offline = SLACalculator.resolve_offline("outage-1", "medium", 20)
        assert offline.threshold_minutes == 60
        assert offline.payment_type == "reward"
        assert offline.amount > 0

    def test_offline_still_validates_mttr(self):
        with pytest.raises(InvalidMTTRError):
            SLACalculator.resolve_offline("outage-1", "low", -5)

    def test_sla_result_model_default_is_false(self):
        result = SLAResult(
            outage_id="outage-1",
            status="met",
            mttr_minutes=10,
            threshold_minutes=60,
            amount=100,
            payment_type="reward",
            rating="good",
        )
        assert result.is_offline_fallback is False