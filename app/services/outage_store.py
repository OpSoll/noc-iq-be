from typing import Dict, List, Optional
from app.models import Outage, outage
from app.services.sla import calculate_sla
from app.models.enums import OutageStatus


class OutageStore:
    """
    Simple in-memory store for outages.
    Data is lost on server restart.
    """

    def __init__(self):
        self._data: Dict[str, Outage] = {}

    def list(self) -> List[Outage]:
        return list(self._data.values())

    def get(self, outage_id: str) -> Optional[Outage]:
        return self._data.get(outage_id)

    def create(self, outage: Outage) -> Outage:
        self._data[outage.id] = outage
        return outage

    def update(self, outage_id: str, outage: Outage) -> Outage:
        self._data[outage_id] = outage
        return outage

    def delete(self, outage_id: str) -> None:
        if outage_id in self._data:
            del self._data[outage_id]
    
    def resolve(self, outage_id: str, mttr_minutes: int):
        outage = self.get(outage_id)
        if not outage:
            return None

        outage.status = OutageStatus.resolved
        outage.mttr_minutes = mttr_minutes

        return outage

# Singleton store instance
outage_store = OutageStore()

def list_violations(self): 
    violations = []

    for outage in self._outages.values():
        if outage.status != OutageStatus.resolved:
            continue

        sla = calculate_sla(
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