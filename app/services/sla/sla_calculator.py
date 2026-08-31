from decimal import Decimal, ROUND_HALF_UP

from app.models.sla import SLAResult, SLASeverityConfig
from .config import SLA_CONFIG, get_all_config, get_config_for_severity
from .errors import InvalidMTTRError, InvalidSLAConfigError


MIN_MTTR_MINUTES = 0
MAX_MTTR_MINUTES = 525600  # 1 year in minutes (#546)

UPTIME_DECIMAL_PLACES = 4
_UPTIME_QUANTUM = Decimal("0.0001")

_SEVERITIES = ("critical", "high", "medium", "low")


def validate_mttr(mttr_minutes: int) -> int:
    """Validate that an MTTR value falls within the supported range (#546).

    Raises ``InvalidMTTRError`` (a ValueError subclass) for out-of-bounds
    or non-numeric input. Returns the validated value on success.
    """
    try:
        in_bounds = MIN_MTTR_MINUTES <= mttr_minutes <= MAX_MTTR_MINUTES
    except TypeError as exc:
        raise InvalidMTTRError(
            f"mttr_minutes must be a number between {MIN_MTTR_MINUTES} and "
            f"{MAX_MTTR_MINUTES}; got {mttr_minutes!r}"
        ) from exc
    if not in_bounds:
        raise InvalidMTTRError(
            f"mttr_minutes must be between {MIN_MTTR_MINUTES} and "
            f"{MAX_MTTR_MINUTES} (inclusive); got {mttr_minutes}"
        )
    return mttr_minutes


def compute_uptime_percentage(available_minutes: int, total_minutes: int) -> str:
    """Compute SLA uptime as a percentage string with 4 decimal places (#548).

    Uses ``decimal.Decimal`` quantized with ROUND_HALF_UP so results are
    free of binary floating point drift, e.g. "99.9500%". An empty window
    (``total_minutes <= 0``, e.g. zero recorded outages) returns
    "100.0000%" and never divides by zero.
    """
    if total_minutes <= 0:
        return f"{Decimal(100).quantize(_UPTIME_QUANTUM)}%"

    available = min(max(Decimal(available_minutes), Decimal(0)), Decimal(total_minutes))
    uptime = (available / Decimal(total_minutes) * Decimal(100)).quantize(
        _UPTIME_QUANTUM, rounding=ROUND_HALF_UP
    )
    return f"{uptime}%"


# --------------------------------------------------------------------------- #
# Issue #551: Maintenance window time deduction helper
# --------------------------------------------------------------------------- #

def deduct_maintenance_window(mttr_minutes: int, maintenance_minutes: int = 0) -> tuple:
    """Deduct overlapping maintenance window minutes from MTTR.

    Returns ``(adjusted_mttr, deducted)`` where ``deducted`` is capped at the
    MTTR value so the adjusted MTTR never goes negative.
    """
    maintenance_minutes = max(0, maintenance_minutes or 0)
    mttr_minutes = max(0, mttr_minutes or 0)
    deducted = min(maintenance_minutes, mttr_minutes)
    return mttr_minutes - deducted, deducted


# --------------------------------------------------------------------------- #
# Issue #550: SLA config version hash collision resistance helper
# --------------------------------------------------------------------------- #

def compute_config_version_hash(config=None) -> str:
    """Compute a SHA-256 digest over the canonical JSON of an SLA config.

    Canonicalisation uses sorted keys and compact separators (matching the
    existing ``_hash_job_payload`` pattern) so the same config always produces
    the same hash regardless of key ordering or whitespace.
    """
    if config is None:
        payload = dict(SLA_CONFIG)
    elif hasattr(config, "model_dump"):
        payload = config.model_dump()
    elif isinstance(config, dict):
        payload = dict(config)
    else:
        payload = vars(config)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Issue #549: SLA breach warning threshold helper (80% of threshold)
# --------------------------------------------------------------------------- #

def sla_warning_threshold_reached(mttr_minutes: int, threshold_minutes: int, warning_fraction: float = 0.8) -> bool:
    """Return True when MTTR reaches the warning fraction of the threshold.

    The warning fires when ``mttr >= threshold * warning_fraction`` but strictly
    before an actual breach (``mttr < threshold``), so a value at/above the
    threshold is treated as a violation rather than a warning.
    """
    if warning_fraction >= 1.0 or threshold_minutes <= 0:
        return False
    return threshold_minutes * warning_fraction <= mttr_minutes < threshold_minutes


class SLACalculator:
    @staticmethod
    def validate_config(config=None) -> dict[str, SLASeverityConfig]:
        """Validate monotonic severity penalty multipliers (#547).

        Asserts ``critical.penalty_per_minute >= high.penalty_per_minute >=
        medium.penalty_per_minute >= low.penalty_per_minute`` and raises
        ``InvalidSLAConfigError`` on violation. ``config`` defaults to
        ``get_all_config()`` and may contain either ``SLASeverityConfig``
        instances or plain dicts.
        """
        cfg = config if config is not None else get_all_config()

        normalized: dict[str, SLASeverityConfig] = {}
        for severity in _SEVERITIES:
            entry = cfg.get(severity)
            if entry is None:
                raise InvalidSLAConfigError(f"Missing SLA config for severity: {severity}")
            normalized[severity] = (
                entry
                if isinstance(entry, SLASeverityConfig)
                else SLASeverityConfig(**dict(entry))
            )

        penalties = [normalized[sev].penalty_per_minute for sev in _SEVERITIES]
        if penalties != sorted(penalties, reverse=True):
            raise InvalidSLAConfigError(
                "SLA penalty multipliers must be monotonically non-increasing "
                "with severity: critical >= high >= medium >= low. "
                f"Got critical={penalties[0]}, high={penalties[1]}, "
                f"medium={penalties[2]}, low={penalties[3]}"
            )
        return normalized

    @classmethod
    def calculate_sla(cls, outage_id: str, severity: str, mttr_minutes: int, policy_version: str = "1.0", threshold_source: str = "config", is_offline_fallback: bool = False) -> SLAResult:
        return cls.calculate(outage_id, severity, mttr_minutes, policy_version, threshold_source, is_offline_fallback)

    @classmethod
    def resolve_offline(cls, outage_id: str, severity: str, mttr_minutes: int, policy_version: str = "1.0", threshold_source: str = "config") -> SLAResult:
        """Compute SLA locally when the on-chain Soroban RPC is unreachable (#545).

        Applies the identical off-chain Python math as the online path and
        tags the result with ``is_offline_fallback=True`` for auditability.
        """
        return cls.calculate(outage_id, severity, mttr_minutes, policy_version, threshold_source, is_offline_fallback=True)

    @staticmethod
    def calculate(outage_id: str, severity: str, mttr_minutes: int, policy_version: str = "1.0", threshold_source: str = "config", is_offline_fallback: bool = False) -> SLAResult:
        validate_mttr(mttr_minutes)
        severity = severity.lower()

        if severity not in SLA_CONFIG:
            raise ValueError(f"Unknown severity level: {severity}")

        # Use policy version to get configuration (for historical recompute)
        try:
            config = get_config_for_severity(severity)
        except ValueError:
            # Fallback to default config if version-specific config not found
            config = SLA_CONFIG[severity]

        threshold = config.threshold_minutes
        asset_code = config.asset_code
        asset_issuer = config.asset_issuer

        # Case 1: SLA violated → penalty
        # Deterministic boundary handling: use >= for violation check to handle exact threshold edges
        if adjusted_mttr > threshold:
            overtime = adjusted_mttr - threshold
            penalty = overtime * config.penalty_per_minute

            return SLAResult(
                outage_id=outage_id,
                status="violated",
                mttr_minutes=adjusted_mttr,
                threshold_minutes=threshold,
                amount=-penalty,
                payment_type="penalty",
                rating="poor",
                policy_version=policy_version,
                threshold_source=threshold_source,
                is_offline_fallback=is_offline_fallback,
                reason_code="mttr_exceeded",
                decision_trace=f"MTTR {adjusted_mttr} > threshold {threshold} (overtime {overtime} minutes)",
                asset_code=asset_code,
                asset_issuer=asset_issuer,
                deducted_maintenance_minutes=deducted_maintenance,
                config_version_hash=config_version_hash,
            )

        # Case 2: SLA met → reward
        # Deterministic boundary handling: use <= for met check to handle exact threshold edges
        performance_ratio = 0 if threshold == 0 else (adjusted_mttr * 100) // threshold

        if performance_ratio < 50:
            multiplier = 200
            rating = "exceptional"
            reason_code = "met_exceptional"
        elif performance_ratio < 75:
            multiplier = 150
            rating = "excellent"
            reason_code = "met_excellent"
        else:
            multiplier = 100
            rating = "good"
            reason_code = "met_good"

        reward = (config.reward_base * multiplier) // 100

        return SLAResult(
            outage_id=outage_id,
            status="met",
            mttr_minutes=adjusted_mttr,
            threshold_minutes=threshold,
            amount=reward,
            payment_type="reward",
            rating=rating,
            policy_version=policy_version,
            threshold_source=threshold_source,
            is_offline_fallback=is_offline_fallback,
            reason_code=reason_code,
            decision_trace=f"MTTR {adjusted_mttr} <= threshold {threshold}, performance ratio {performance_ratio}%, rating {rating}",
            asset_code=asset_code,
            asset_issuer=asset_issuer,
            deducted_maintenance_minutes=deducted_maintenance,
            config_version_hash=config_version_hash,
        )