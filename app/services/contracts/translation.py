from app.models.sla import SLAResult


def translate_contract_result(raw_result: dict) -> SLAResult:
    return SLAResult(
        outage_id=raw_result["outage_id"],
        status="violated" if raw_result["status"] == "viol" else "met",
        mttr_minutes=raw_result["mttr_minutes"],
        threshold_minutes=raw_result["threshold_minutes"],
        amount=raw_result["amount"],
        payment_type="penalty" if raw_result["payment_type"] == "pen" else "reward",
        rating={
            "top": "exceptional",
            "high": "excellent",
            "good": "good",
            "poor": "poor",
        }[raw_result["rating"]],
    )
