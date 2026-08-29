from dataclasses import dataclass
from enum import Enum


class SLATier(str, Enum):
    """Structured classification of SLA availability tiers.

    Tier thresholds are the guaranteed monthly availability targets
    (percent of uptime). Each tier also carries a penalty rate applied
    when the observed availability falls short of the tier threshold.
    """

    TIER_1 = "tier_1"  # 99.99% availability
    TIER_2 = "tier_2"  # 99.9% availability
    TIER_3 = "tier_3"  # 99.0% availability


@dataclass(frozen=True)
class SLATierConfig:
    """Thresholds and penalty rate for a single SLA availability tier.

    ``penalty_rate`` is expressed in penalty credits per percentage point
    of availability shortfall below the tier threshold. The 100/50/25
    ladder mirrors the repo's existing severity penalty conventions
    (critical/high/medium) from ``app/services/sla/config.py``.
    """

    tier: SLATier
    availability_threshold: float
    penalty_rate: float


SLATIER_CONFIG: dict[SLATier, SLATierConfig] = {
    SLATier.TIER_1: SLATierConfig(
        tier=SLATier.TIER_1,
        availability_threshold=99.99,
        penalty_rate=100.0,
    ),
    SLATier.TIER_2: SLATierConfig(
        tier=SLATier.TIER_2,
        availability_threshold=99.9,
        penalty_rate=50.0,
    ),
    SLATier.TIER_3: SLATierConfig(
        tier=SLATier.TIER_3,
        availability_threshold=99.0,
        penalty_rate=25.0,
    ),
}


def get_tier_config(tier: SLATier) -> SLATierConfig:
    """Return the config mapping for an SLA availability tier."""
    if tier not in SLATIER_CONFIG:
        raise ValueError(f"Unknown SLA tier: {tier!r}")
    return SLATIER_CONFIG[tier]


def classify_availability(availability_pct: float) -> SLATier:
    """Classify an observed availability percentage into the strictest SLA tier.

    The returned tier is the highest (strictest) tier whose availability
    threshold is met or exceeded. Availability below the TIER_3 threshold
    is not eligible for any SLA tier and raises ``ValueError``.
    """
    for tier in (SLATier.TIER_1, SLATier.TIER_2, SLATier.TIER_3):
        threshold = SLATIER_CONFIG[tier].availability_threshold
        if availability_pct >= threshold:
            return tier
    raise ValueError(
        f"Availability {availability_pct}% is below the minimum SLA tier "
        f"threshold ({SLATIER_CONFIG[SLATier.TIER_3].availability_threshold}%)"
    )
