"""Unit tests for SLA availability tier classification (issue #557).

Verifies:
- SLATier enum member presence and values
- Per-tier availability threshold and penalty rate mapping
- classify_availability() boundary and error handling
"""
import pytest

from app.services.sla.tiers import (
    SLATIER_CONFIG,
    SLATier,
    SLATierConfig,
    classify_availability,
    get_tier_config,
)


# ---------------------------------------------------------------------------
# Enum structure
# ---------------------------------------------------------------------------

class TestSLATierEnum:
    """SLATier enum must expose exactly the three required tiers."""

    def test_required_tiers_exist(self):
        assert list(SLATier) == [
            SLATier.TIER_1,
            SLATier.TIER_2,
            SLATier.TIER_3,
        ]

    def test_tier_values(self):
        assert SLATier.TIER_1.value == "tier_1"
        assert SLATier.TIER_2.value == "tier_2"
        assert SLATier.TIER_3.value == "tier_3"

    def test_tiers_are_string_enums(self):
        assert isinstance(SLATier.TIER_1, str)


# ---------------------------------------------------------------------------
# Tier mapping: thresholds + penalty rates
# ---------------------------------------------------------------------------

class TestTierMapping:
    """Each tier must map to a threshold and a penalty rate."""

    EXPECTED = {
        SLATier.TIER_1: {"availability_threshold": 99.99, "penalty_rate": 100.0},
        SLATier.TIER_2: {"availability_threshold": 99.9, "penalty_rate": 50.0},
        SLATier.TIER_3: {"availability_threshold": 99.0, "penalty_rate": 25.0},
    }

    def test_every_tier_has_a_config(self):
        assert set(SLATIER_CONFIG.keys()) == set(SLATier)

    def test_all_configs_are_sla_tier_configs(self):
        for config in SLATIER_CONFIG.values():
            assert isinstance(config, SLATierConfig)

    def test_thresholds_and_penalty_rates(self):
        for tier, expected in self.EXPECTED.items():
            config = SLATIER_CONFIG[tier]
            assert config.tier == tier
            assert config.availability_threshold == pytest.approx(
                expected["availability_threshold"]
            )
            assert config.penalty_rate == pytest.approx(expected["penalty_rate"])

    def test_thresholds_are_strictly_descending(self):
        thresholds = [
            SLATIER_CONFIG[tier].availability_threshold
            for tier in (SLATier.TIER_1, SLATier.TIER_2, SLATier.TIER_3)
        ]
        assert thresholds == sorted(thresholds, reverse=True)
        assert len(set(thresholds)) == len(thresholds)

    def test_get_tier_config_round_trips(self):
        for tier in SLATier:
            assert get_tier_config(tier) is SLATIER_CONFIG[tier]

    def test_get_tier_config_rejects_unknown(self):
        with pytest.raises(ValueError):
            get_tier_config("tier_4")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tier mapping: availability classification
# ---------------------------------------------------------------------------

class TestClassifyAvailability:
    """classify_availability() must pick the strictest satisfied tier."""

    def test_exact_threshold_boundaries(self):
        assert classify_availability(99.99) == SLATier.TIER_1
        assert classify_availability(99.9) == SLATier.TIER_2
        assert classify_availability(99.0) == SLATier.TIER_3

    def test_above_threshold_classifies_to_strictest_tier(self):
        assert classify_availability(99.995) == SLATier.TIER_1
        assert classify_availability(100.0) == SLATier.TIER_1
        assert classify_availability(99.95) == SLATier.TIER_2
        assert classify_availability(99.91) == SLATier.TIER_2
        assert classify_availability(99.05) == SLATier.TIER_3
        assert classify_availability(99.01) == SLATier.TIER_3

    def test_just_below_threshold_falls_to_next_tier(self):
        assert classify_availability(99.9899) == SLATier.TIER_2
        assert classify_availability(99.89) == SLATier.TIER_3

    def test_below_all_thresholds_raises(self):
        with pytest.raises(ValueError):
            classify_availability(98.99)

    def test_zero_availability_raises(self):
        with pytest.raises(ValueError):
            classify_availability(0.0)
