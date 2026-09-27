from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.base_class import Base

class SiteHierarchy(Base):
    __tablename__ = "site_hierarchy"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    region = Column(String, index=True, nullable=False)
    parent_id = Column(Integer, ForeignKey("site_hierarchy.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    parent = relationship("SiteHierarchy", remote_side=[id], backref="children")
