from sqlalchemy.orm import Session
from app.models.dispute import SlaDispute
from app.schemas.dispute import DisputeCreate, DisputeUpdate

class DisputeService:
    def open_dispute(self, db: Session, dispute_in: DisputeCreate) -> SlaDispute:
        db_dispute = SlaDispute(**dispute_in.dict())
        db.add(db_dispute)
        db.commit()
        db.refresh(db_dispute)
        return db_dispute

    def update_dispute_state(self, db: Session, dispute_id: int, update_in: DisputeUpdate) -> SlaDispute:
        db_dispute = db.query(SlaDispute).filter(SlaDispute.id == dispute_id).first()
        if not db_dispute:
            return None
        
        db_dispute.state = update_in.state.value
        if update_in.resolution_notes:
            db_dispute.resolution_notes = update_in.resolution_notes
            
        db.commit()
        db.refresh(db_dispute)
        return db_dispute
