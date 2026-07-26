from datetime import datetime, timezone
from typing import Dict, List, Optional
from app.models import Outage
from app.services.sla import SLACalculator
from app.models.enums import OutageStatus, Severity
from app.services.soft_delete import SoftDeleteMixin


class OutageStore(SoftDeleteMixin):
    """
    Simple in-memory store for outages with soft-delete support.
    Data is lost on server restart.
    """

    def __init__(self):
        self._data: Dict[str, Outage] = {}

    def list(
        self,
        severity: Severity | None = None,
        status: OutageStatus | None = None,
        page: int = 1,
        page_size: int = 20,
        include_deleted: bool = False,
    ):
        items = list(self._data.values())

        items = self._filter_deleted(items, include_deleted=include_deleted)

        if severity:
            items = [o for o in items if o.severity == severity]

        if status:
            items = [o for o in items if o.status == status]

        total = len(items)

        start = (page - 1) * page_size
        end = start + page_size

        return {
            "items": items[start:end],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def get(self, outage_id: str) -> Optional[Outage]:
        return self._data.get(outage_id)

    def create(self, outage: Outage) -> Outage:
        self._data[outage.id] = outage
        return outage

    def update(self, outage_id: str, outage: Outage) -> Outage:
        self._data[outage_id] = outage
        return outage

    def delete(self, outage_id: str) -> None:
        self._data.pop(outage_id, None)

    def resolve(self, outage_id: str, mttr_minutes: int):
        outage = self.get(outage_id)
        if not outage:
            return None

        outage.status = OutageStatus.resolved
        outage.mttr_minutes = mttr_minutes
        return outage

    def list_violations(self):
        violations = []

        for outage in self._data.values():
            if self._is_deleted(outage):
                continue
            if outage.status != OutageStatus.resolved:
                continue

            sla = SLACalculator.calculate(
                outage_id=outage.id,
                severity=outage.severity.value,
                mttr_minutes=outage.mttr_minutes,
            )

            if sla["status"] == "violated":
                violations.append({
                    "outage": outage,
                    "sla": sla,
                })

        return violations


# Singleton instance
outage_store = OutageStore()
