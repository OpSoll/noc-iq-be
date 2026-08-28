"""SLA dashboard metric definition registry (BE-W5-106 / issue #367).

Each KPI has a named formula, its input dependencies, and a description that
FE and ops teams can rely on for alignment.  The registry is the single source
of truth for all dashboard metric computations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Sequence


@dataclass(frozen=True)
class MetricDefinition:
    """Immutable descriptor for a single dashboard KPI."""

    name: str
    """Unique snake_case identifier used in API responses and FE keys."""

    description: str
    """Human-readable explanation of what the metric represents."""

    inputs: List[str]
    """Names of input fields required to compute this metric."""

    formula: Callable[..., Any]
    """Pure function implementing the metric computation."""

    unit: str = ""
    """Optional unit label (e.g. '%', 'minutes', 'count', 'currency')."""


# ---------------------------------------------------------------------------
# Formula implementations
# ---------------------------------------------------------------------------

def _violation_rate(total_outages: int, total_violations: int) -> float:
    """Fraction of outages that violated the SLA threshold.

    Formula: total_violations / total_outages  (returns 0.0 when no outages)
    Range:   [0.0, 1.0]
    """
    if total_outages == 0:
        return 0.0
    return total_violations / total_outages


def _net_payout(total_rewards: float, total_penalties: float) -> float:
    """Net financial exposure from SLA outcomes.

    Formula: total_rewards - total_penalties
    Positive → net reward position; negative → net penalty position.
    """
    return total_rewards - total_penalties


def _avg_mttr(total_mttr_minutes: float, total_resolved: int) -> float:
    """Mean Time To Resolution across resolved outages (minutes).

    Formula: sum(mttr_minutes) / count(resolved_outages)
    Returns 0.0 when no resolved outages.
    """
    if total_resolved == 0:
        return 0.0
    return total_mttr_minutes / total_resolved


def _availability(downtime_minutes: float, period_minutes: float) -> float:
    """Uptime availability percentage for the measurement period.

    Formula: (period_minutes - downtime_minutes) / period_minutes × 100
    Clamped to [0.0, 100.0].
    """
    if period_minutes <= 0:
        return 100.0
    raw = (period_minutes - downtime_minutes) / period_minutes * 100.0
    return max(0.0, min(100.0, raw))


def _penalty_exposure(total_penalties: float, total_outages: int) -> float:
    """Average penalty cost per outage.

    Formula: total_penalties / total_outages  (returns 0.0 when no outages)
    """
    if total_outages == 0:
        return 0.0
    return total_penalties / total_outages


def _reward_per_met(total_rewards: float, met_count: int) -> float:
    """Average reward per met-SLA outage.

    Formula: total_rewards / met_count  (returns 0.0 when no met outages)
    """
    if met_count == 0:
        return 0.0
    return total_rewards / met_count


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

METRIC_REGISTRY: Dict[str, MetricDefinition] = {
    m.name: m
    for m in [
        MetricDefinition(
            name="violation_rate",
            description=(
                "Fraction of outages in the period that violated the configured SLA "
                "threshold.  A rising value signals degrading service quality."
            ),
            inputs=["total_outages", "total_violations"],
            formula=_violation_rate,
            unit="%",
        ),
        MetricDefinition(
            name="net_payout",
            description=(
                "Net financial exposure: positive means net reward, negative means net "
                "penalty.  Used by finance to reconcile expected Stellar settlements."
            ),
            inputs=["total_rewards", "total_penalties"],
            formula=_net_payout,
            unit="currency",
        ),
        MetricDefinition(
            name="avg_mttr",
            description=(
                "Mean Time To Resolution in minutes, averaged across all resolved "
                "outages in the period.  Primary leading indicator for SLA health."
            ),
            inputs=["total_mttr_minutes", "total_resolved"],
            formula=_avg_mttr,
            unit="minutes",
        ),
        MetricDefinition(
            name="availability",
            description=(
                "Uptime percentage for the period, derived from cumulative downtime.  "
                "Standard telecom SLA anchor metric."
            ),
            inputs=["downtime_minutes", "period_minutes"],
            formula=_availability,
            unit="%",
        ),
        MetricDefinition(
            name="penalty_exposure",
            description=(
                "Average penalty cost per outage event.  "
                "Useful for cost-risk modelling and capacity planning."
            ),
            inputs=["total_penalties", "total_outages"],
            formula=_penalty_exposure,
            unit="currency",
        ),
        MetricDefinition(
            name="reward_per_met",
            description=(
                "Average reward per outage that met its SLA threshold.  "
                "Incentive alignment signal for NOC operations teams."
            ),
            inputs=["total_rewards", "met_count"],
            formula=_reward_per_met,
            unit="currency",
        ),
    ]
}


def get_metric(name: str) -> MetricDefinition:
    """Return a MetricDefinition by name.

    Raises:
        KeyError: if the metric is not registered.
    """
    try:
        return METRIC_REGISTRY[name]
    except KeyError:
        raise KeyError(f"Unknown metric '{name}'. Available: {list(METRIC_REGISTRY)}")


def list_metrics() -> List[MetricDefinition]:
    """Return all registered metric definitions sorted by name."""
    return sorted(METRIC_REGISTRY.values(), key=lambda m: m.name)


def compute_metric(name: str, **kwargs: Any) -> Any:
    """Compute a registered metric from named input kwargs.

    Raises:
        KeyError: if metric name is not registered.
        TypeError: if required inputs are missing.
    """
    metric = get_metric(name)
    missing = [inp for inp in metric.inputs if inp not in kwargs]
    if missing:
        raise TypeError(f"Missing inputs for metric '{name}': {missing}")
    ordered = {k: kwargs[k] for k in metric.inputs}
    return metric.formula(**ordered)
