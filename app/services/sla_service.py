from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.orm.outage import OutageORM
from app.models.orm.sla import SLAResultORM


def compute_device_sla(db: Session, device_id: str, period: str) -> dict:
    """
    Minimal runtime-safe SLA aggregation for jobs.

    The current backend has no dedicated device table, so we treat `device_id`
    as an outage/site identifier and aggregate matching outages and their latest
    SLA results.
    """
    outages = (
        db.query(OutageORM)
        .filter(
            (OutageORM.site_id == device_id)
            | (OutageORM.id == device_id)
            | (OutageORM.site_name == device_id)
        )
        .all()
    )

    outage_ids = [outage.id for outage in outages]
    if not outage_ids:
        return {
            "device_id": device_id,
            "period": period,
            "total_outages": 0,
            "violated_outages": 0,
            "avg_mttr_minutes": 0.0,
            "is_violated": False,
        }

    latest_results = {}
    rows = (
        db.query(SLAResultORM)
        .filter(SLAResultORM.outage_id.in_(outage_ids))
        .order_by(SLAResultORM.outage_id, SLAResultORM.created_at.desc(), SLAResultORM.id.desc())
        .all()
    )
    for row in rows:
        latest_results.setdefault(row.outage_id, row)

    violated_outages = sum(1 for result in latest_results.values() if result.status == "violated")
    mttr_values = [result.mttr_minutes for result in latest_results.values()]
    avg_mttr = 0.0 if not mttr_values else round(sum(mttr_values) / len(mttr_values), 2)

    return {
        "device_id": device_id,
        "period": period,
        "total_outages": len(outage_ids),
        "violated_outages": violated_outages,
        "avg_mttr_minutes": avg_mttr,
        "is_violated": violated_outages > 0,
    }
