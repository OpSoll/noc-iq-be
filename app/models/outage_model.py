from sqlalchemy import Column, Integer, String, Index
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class Outage(Base):
    __tablename__ = "outages"
    id = Column(Integer, primary_key=True)
    status = Column(String(50))
    region = Column(String(50))
    severity = Column(Integer)

    # Composite index for optimization
    __table_args__ = (
        Index('ix_outages_status_region_severity', 'status', 'region', 'severity'),
    )
