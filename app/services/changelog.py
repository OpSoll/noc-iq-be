from sqlalchemy.orm import Session
from app.models.changelog import ChangelogEntry
from app.schemas.changelog import ChangelogEntryCreate
from typing import List

class ChangelogService:
    def get_latest_changes(self, db: Session, limit: int = 10) -> List[ChangelogEntry]:
        return db.query(ChangelogEntry).order_by(ChangelogEntry.release_date.desc()).limit(limit).all()

    def add_entry(self, db: Session, entry_in: ChangelogEntryCreate) -> ChangelogEntry:
        db_entry = ChangelogEntry(**entry_in.dict())
        db.add(db_entry)
        db.commit()
        db.refresh(db_entry)
        return db_entry
