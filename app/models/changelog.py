from sqlalchemy import Column, Integer, String, Boolean, DateTime
from datetime import datetime
from app.db.base_class import Base

class ChangelogEntry(Base):
    __tablename__ = "api_changelog"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True)
    version = Column(String, index=True, nullable=False, unique=True)
    description = Column(String, nullable=False)
    breaking_changes = Column(Boolean, default=False)
    release_date = Column(DateTime, default=datetime.utcnow)
