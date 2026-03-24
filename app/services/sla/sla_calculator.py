from app.models import SLAResult
from .config import SLA_CONFIG


class SLACalculator:
    @staticmethod
    def calculate(outage_id: str, severity: str, mttr_minutes: int) -> SLAResult:
        severity = severity.lower()

        if severity not in SLA_CONFIG:
            raise ValueError(f"Unknown severity level: {severity}")

        config = SLA_CONFIG[severity]
        threshold = config["threshold_minutes"]

        # Case 1: SLA violated → penalty
        if mttr_minutes > threshold:
            overtime = mttr_minutes - threshold
            penalty = overtime * config["penalty_per_minute"]

            return SLAResult(
                outage_id=outage_id,
                status="violated",
                mttr_minutes=mttr_minutes,
                threshold_minutes=threshold,
                amount=-penalty,
                payment_type="penalty",
                rating="poor",
            )

        # Case 2: SLA met → reward
        performance_ratio = 0 if threshold == 0 else (mttr_minutes * 100) // threshold

        if performance_ratio < 50:
            multiplier = 200
            rating = "exceptional"
        elif performance_ratio < 75:
            multiplier = 150
            rating = "excellent"
        else:
            multiplier = 100
            rating = "good"

        reward = (config["reward_base"] * multiplier) // 100

        return SLAResult(
            outage_id=outage_id,
            status="met",
            mttr_minutes=mttr_minutes,
            threshold_minutes=threshold,
            amount=reward,
            payment_type="reward",
            rating=rating,
        )
