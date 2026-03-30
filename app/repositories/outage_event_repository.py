import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.orm.outage_event import OutageEventORM


class OutageEventRepository:
    def __init__(self, db: Session):
        self.db = db

    def record(self, outage_id: str, event_type: str, detail: Optional[Dict[str, Any]] = None) -> OutageEventORM:
        orm = OutageEventORM(
            id=f"evt_{uuid4().hex[:12]}",
            outage_id=outage_id,
            event_type=event_type,
            detail=json.dumps(detail) if detail else None,
            occurred_at=datetime.utcnow(),
        )
        self.db.add(orm)
        self.db.commit()
        self.db.refresh(orm)
        return orm

    def list_for_outage(self, outage_id: str) -> List[Dict[str, Any]]:
        rows = (
            self.db.query(OutageEventORM)
            .filter(OutageEventORM.outage_id == outage_id)
            .order_by(OutageEventORM.occurred_at.asc())
            .all()
        )
        return [
            {
                "id": r.id,
                "outage_id": r.outage_id,
                "event_type": r.event_type,
                "detail": json.loads(r.detail) if r.detail else None,
                "occurred_at": r.occurred_at.isoformat(),
            }
            for r in rows
        ]
