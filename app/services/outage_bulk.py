from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from app.models.outage_bulk import OutageRecord
from app.schemas.outage_bulk import BulkOutageCreate, BulkOutageResponse

class OutageBulkService:
    def create_bulk_outages(self, db: Session, payload: BulkOutageCreate) -> BulkOutageResponse:
        try:
            records = [
                OutageRecord(
                    service_name=item.service_name,
                    description=item.description,
                    severity=item.severity
                )
                for item in payload.outages
            ]
            db.add_all(records)
            db.commit()
            return BulkOutageResponse(
                successful=len(records),
                failed=0,
                message="Successfully created bulk outages"
            )
        except SQLAlchemyError as e:
            db.rollback()
            return BulkOutageResponse(
                successful=0,
                failed=len(payload.outages),
                message=f"Database transaction rolled back due to error: {str(e)}"
            )
