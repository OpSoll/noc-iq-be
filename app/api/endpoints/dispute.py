from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.api import deps
from app.core.config import settings
from app.models.orm.outage import OutageORM
from app.schemas.dispute import DisputeCreate, DisputeUpdate, DisputeResponse, ReSimulateRequest
from app.services.contracts import SLAContractAdapter, translate_contract_result
from app.services.dispute import DisputeService

router = APIRouter()
service = DisputeService()

@router.post("/", response_model=DisputeResponse)
def open_sla_dispute(
    *,
    db: Session = Depends(deps.get_db),
    dispute_in: DisputeCreate,
):
    """
    Open a new SLA dispute.
    """
    return service.open_dispute(db, dispute_in=dispute_in)

@router.patch("/{dispute_id}", response_model=DisputeResponse)
def update_sla_dispute(
    *,
    db: Session = Depends(deps.get_db),
    dispute_id: int,
    update_in: DisputeUpdate,
):
    """
    Update SLA dispute state and resolution notes.
    """
    dispute = service.update_dispute_state(db, dispute_id=dispute_id, update_in=update_in)
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")
    return dispute


@router.post("/{dispute_id}/re-simulate")
def re_simulate_dispute(
    *,
    db: Session = Depends(deps.get_db),
    dispute_id: str,
    payload: ReSimulateRequest,
):
    """Dry-run the Soroban ``calculate_sla`` contract call for a dispute (Issue #510).

    Re-simulates the SLA calculation with updated MTTR values and returns a
    comparison of the original vs. the simulated penalty amount. The contract
    invocation runs as an RPC dry-run (``CONTRACT_EXECUTION_MODE``), so no
    state is written on chain.

    Raw SQL is used for the dispute/SLA lookups because the ``sla_disputes``
    table is mapped by both the legacy and the current ORM model.
    """
    dispute_row = db.execute(
        text(
            "SELECT id, sla_result_id "
            "FROM sla_disputes WHERE id = :dispute_id"
        ),
        {"dispute_id": str(dispute_id)},
    ).mappings().first()
    if not dispute_row:
        raise HTTPException(status_code=404, detail="Dispute not found")

    sla_result_id = dispute_row["sla_result_id"]
    if sla_result_id is None:
        raise HTTPException(status_code=404, detail="Dispute has no SLA result to re-simulate")

    sla_row = db.execute(
        text(
            "SELECT outage_id, status, mttr_minutes, threshold_minutes, amount, payment_type "
            "FROM sla_results WHERE id = :sla_result_id"
        ),
        {"sla_result_id": sla_result_id},
    ).mappings().first()
    if not sla_row:
        raise HTTPException(status_code=404, detail="SLA result not found")

    outage = (
        db.query(OutageORM)
        .filter(OutageORM.id == sla_row["outage_id"])
        .first()
    )
    if not outage:
        raise HTTPException(status_code=404, detail="Outage not found")

    severity = payload.severity or outage.severity
    raw_contract_result = SLAContractAdapter.calculate_sla(
        outage_id=outage.id,
        severity=severity,
        mttr_minutes=payload.mttr_minutes,
        policy_version="1.0",
        threshold_source="config",
    )
    simulated = translate_contract_result(raw_contract_result)

    original_penalty = sla_row["amount"] if sla_row["payment_type"] == "penalty" else 0.0
    simulated_penalty = simulated.amount if simulated.payment_type == "penalty" else 0.0

    mode = "soroban_rpc_dry_run" if settings.CONTRACT_EXECUTION_MODE == "soroban_rpc" else "local_dry_run"
    return {
        "dispute_id": str(dispute_row["id"]),
        "outage_id": outage.id,
        "mode": mode,
        "original": {
            "status": sla_row["status"],
            "mttr_minutes": sla_row["mttr_minutes"],
            "threshold_minutes": sla_row["threshold_minutes"],
            "amount": sla_row["amount"],
            "payment_type": sla_row["payment_type"],
        },
        "simulated": {
            "status": simulated.status,
            "mttr_minutes": simulated.mttr_minutes,
            "threshold_minutes": simulated.threshold_minutes,
            "amount": simulated.amount,
            "payment_type": simulated.payment_type,
            "rating": simulated.rating,
        },
        "penalty_delta": round(simulated_penalty - original_penalty, 6),
    }
