from typing import Optional

from app.models import SLAResult
from .config import SLA_CONFIG, get_config_for_severity


# Exceptional uptime threshold for the reward credit tier (issue #552)
EXCEPTIONAL_UPTIME_PCT = 99.99

# Fields compared when auditing historical SLA recalculation diffs (issue #553)
COMPARISON_FIELDS = (
    "outage_id",
    "status",
    "mttr_minutes",
    "threshold_minutes",
    "amount",
    "payment_type",
    "rating",
    "policy_version",
    "threshold_source",
    "reason_code",
    "decision_trace",
    "asset_code",
    "asset_issuer",
    "penalty_capped",
)


def deduplicate_outage_ids(outage_ids):
    """Deduplicate a batch outage ID list preserving first-occurrence order (issue #554).

    Returns a tuple of ``(unique_ids, skipped_ids)`` where ``skipped_ids`` is the
    list of duplicate outage IDs that were skipped so callers can emit a warning.
    """
    unique_ids = []
    skipped_ids = []
    seen = set()
    for outage_id in outage_ids:
        if outage_id in seen:
            skipped_ids.append(outage_id)
        else:
            seen.add(outage_id)
            unique_ids.append(outage_id)
    return unique_ids, skipped_ids


class SLACalculator:
    @classmethod
    def calculate_reward_credit(cls, severity: str, uptime_pct: float) -> dict:
        """Calculate the exceptional uptime reward credit for a severity (issue #552).

        A reward credit is only granted when the observed monthly uptime exceeds
        99.99%. The credit amount is derived from the severity's ``reward_base``
        config value. When uptime is at or below the exceptional threshold no
        exceptional credit is granted.
        """
        severity = severity.lower()

        if severity not in SLA_CONFIG:
            raise ValueError(f"Unknown severity level: {severity}")

        config = get_config_for_severity(severity)

        if uptime_pct <= EXCEPTIONAL_UPTIME_PCT:
            # Not exceptional this period: no reward credit, met SLA by uptime
            return {
                "severity": severity,
                "uptime_pct": uptime_pct,
                "reward_granted": False,
                "credit": 0,
                "payment_type": "reward",
                "rating": "good",
                "reason_code": "uptime_met",
                "decision_trace": (
                    f"Uptime {uptime_pct}% <= {EXCEPTIONAL_UPTIME_PCT}% "
                    "exceptional threshold, no exceptional reward credit"
                ),
            }

        credit = config.reward_base
        return {
            "severity": severity,
            "uptime_pct": uptime_pct,
            "reward_granted": True,
            "credit": credit,
            "payment_type": "reward",
            "rating": "exceptional",
            "reason_code": "uptime_exceptional",
            "decision_trace": (
                f"Uptime {uptime_pct}% > {EXCEPTIONAL_UPTIME_PCT}% "
                f"exceptional threshold, reward credit {credit} "
                f"based on reward_base {config.reward_base}"
            ),
        }

    @staticmethod
    def _to_comparison_dict(value) -> dict:
        """Normalize an SLAResult (or mapping) into a plain dict for comparison."""
        if isinstance(value, SLAResult):
            return value.model_dump()
        return dict(value)

    @classmethod
    def compare_results(cls, original, recalculated) -> dict:
        """Compare an original SLA result against a recalculated one (issue #553).

        Returns a detailed diff object:
            {
                "changed": [field names that differ],
                "original": {full original payload},
                "recalculated": {full recalculated payload},
                "differences": {field: {"original": ..., "recalculated": ...}},
            }
        The comparison summary is also written to the audit trail. Logging is
        defensive so audit failures never break the calculator.
        """
        original_data = cls._to_comparison_dict(original)
        recalculated_data = cls._to_comparison_dict(recalculated)

        changed = []
        differences = {}
        for field in COMPARISON_FIELDS:
            original_value = original_data.get(field)
            recalculated_value = recalculated_data.get(field)
            if original_value != recalculated_value:
                changed.append(field)
                differences[field] = {
                    "original": original_value,
                    "recalculated": recalculated_value,
                }

        summary = {
            "changed": changed,
            "original": original_data,
            "recalculated": recalculated_data,
            "differences": differences,
        }

        # Record a comparison summary in the audit trail (issue #553)
        try:
            from app.services.audit_log import audit_log

            audit_log.log(
                event_type="sla.calculation.compare",
                details={
                    "outage_id": recalculated_data.get("outage_id"),
                    "changed_fields": changed,
                    "changed_count": len(changed),
                    "summary": summary,
                },
            )
        except Exception:
            # Audit logging must never break the calculator
            pass

        return summary

    @classmethod
    def calculate_sla(
        cls,
        outage_id: str,
        severity: str,
        mttr_minutes: int,
        policy_version: str = "1.0",
        threshold_source: str = "config",
        monthly_contract_fee: Optional[int] = None,
        max_penalty: Optional[int] = None,
    ) -> SLAResult:
        return cls.calculate(
            outage_id,
            severity,
            mttr_minutes,
            policy_version,
            threshold_source,
            monthly_contract_fee=monthly_contract_fee,
            max_penalty=max_penalty,
        )

    @staticmethod
    def calculate(
        outage_id: str,
        severity: str,
        mttr_minutes: int,
        policy_version: str = "1.0",
        threshold_source: str = "config",
        monthly_contract_fee: Optional[int] = None,
        max_penalty: Optional[int] = None,
    ) -> SLAResult:
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
        if mttr_minutes > threshold:
            overtime = mttr_minutes - threshold
            penalty = overtime * config.penalty_per_minute

            # Penalty cap per billing period: capped at 100% of the site's
            # monthly contract fee (issue #555). Fall back to max_penalty when
            # provided, otherwise to monthly_contract_fee; no cap when neither
            # is supplied so existing behavior is preserved.
            penalty_capped = False
            cap = max_penalty if max_penalty is not None else monthly_contract_fee
            if cap is not None and penalty > cap:
                penalty = cap
                penalty_capped = True

            decision_trace = f"MTTR {mttr_minutes} > threshold {threshold} (overtime {overtime} minutes)"
            if penalty_capped:
                decision_trace += f" | penalty capped at {cap} (100% of monthly contract fee)"

            return SLAResult(
                outage_id=outage_id,
                status="violated",
                mttr_minutes=mttr_minutes,
                threshold_minutes=threshold,
                amount=-penalty,
                payment_type="penalty",
                rating="poor",
                policy_version=policy_version,
                threshold_source=threshold_source,
                reason_code="mttr_exceeded",
                decision_trace=decision_trace,
                asset_code=asset_code,
                asset_issuer=asset_issuer,
                penalty_capped=penalty_capped,
            )

        # Case 2: SLA met → reward
        # Deterministic boundary handling: use <= for met check to handle exact threshold edges
        performance_ratio = 0 if threshold == 0 else (mttr_minutes * 100) // threshold

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
            mttr_minutes=mttr_minutes,
            threshold_minutes=threshold,
            amount=reward,
            payment_type="reward",
            rating=rating,
            policy_version=policy_version,
            threshold_source=threshold_source,
            reason_code=reason_code,
            decision_trace=f"MTTR {mttr_minutes} <= threshold {threshold}, performance ratio {performance_ratio}%, rating {rating}",
            asset_code=asset_code,
            asset_issuer=asset_issuer,
            penalty_capped=False,
        )