from typing import Dict, List, Optional
from app.models import Outage, outage


class OutageStore:
    """
    Simple in-memory store for outages.
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
):
    items = list(self._outages.values())

    # Apply filters (if any already exist)
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
