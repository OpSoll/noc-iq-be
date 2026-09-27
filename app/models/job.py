import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel as PydanticBaseModel
from sqlalchemy import Boolean, Column, DateTime, Enum, Enum as SAEnum, Float, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
import enum

from app.db.base_class import Base


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    STARTED = "started"
    SUCCESS = "success"
    FAILURE = "failure"
    REVOKED = "revoked"
    QUARANTINED = "quarantined"  # BE-W5-054: poison-message quarantine
    DEAD_LETTER = "dead_letter"  # BE-W5-048: terminal dead-letter for exhausted retries


class JobType(str, enum.Enum):
    SLA_COMPUTATION = "sla_computation"
    WEBHOOK_DISPATCH = "webhook_dispatch"
    BULK_SLA_COMPUTATION = "bulk_sla_computation"
    WEBHOOK_DR_REPLAY = "webhook_dr_replay"  # BE-W5-045: disaster-recovery replay


class RetryClass(str, enum.Enum):
    """BE-W5-048: Standardised retry taxonomy per job type."""
    AT_MOST_ONCE = "at_most_once"          # No automatic retry; manual only
    AT_LEAST_ONCE = "at_least_once"         # Retry until success (with backoff cap)
    EXPONENTIAL_BACKOFF = "exponential_backoff"  # Progressive backoff with jitter


# --------------------------------------------------------------------------- #
# BE-W5-050: Standardised job result envelope                                  #
# --------------------------------------------------------------------------- #

class JobErrorDetail(PydanticBaseModel):
    """Structured error metadata carried inside the job result envelope."""
    code: str                                    # Machine-readable error code (e.g. "SLA_TIMEOUT")
    message: str                                 # Human-readable description
    retryable: bool = False                      # Whether a retry could succeed
    details: Optional[Dict[str, Any]] = None     # Optional contextual payload


class JobResultEnvelope(PydanticBaseModel):
    """Standard envelope returned by every job status endpoint.

    BE-W5-050: Operators and FE clients can parse job outcomes uniformly
    regardless of ``job_type``.  The envelope is always present even when
    the job is still running (``status`` will be ``pending``/``started`` and
    ``result``/``error`` will be ``None``).
    """
    job_id: str
    celery_task_id: str
    job_type: str
    status: str
    progress: float = 0.0
    result: Optional[Dict[str, Any]] = None
    error: Optional[JobErrorDetail] = None
    retry_count: int = 0
    max_retries: int = 3
    retry_class: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    created_at: Optional[str] = None


class Job(Base):
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    celery_task_id = Column(String(255), unique=True, nullable=False, index=True)
    job_type = Column(SAEnum(JobType), nullable=False)
    status = Column(SAEnum(JobStatus), default=JobStatus.PENDING, nullable=False)
    payload = Column(Text, nullable=True)        # JSON-encoded input params
    result = Column(Text, nullable=True)         # JSON-encoded result
    error = Column(Text, nullable=True)
    # BE-W5-050: typed error metadata
    error_code = Column(String(64), nullable=True, index=True)
    error_retryable = Column(Boolean, nullable=True)
    error_details = Column(JSON, nullable=True)
    progress = Column(Float, default=0.0)        # 0.0 – 100.0
    progress_details = Column(JSON, nullable=True)  # Structured progress information
    partial_results = Column(JSON, nullable=True)   # Partial results for bulk operations
    per_item_errors = Column(JSON, nullable=True)   # Per-item error tracking
    # BE-041: Retry tracking
    retry_count = Column(Integer, default=0, nullable=False)  # Number of times job has been retried
    max_retries = Column(Integer, default=3, nullable=False)  # Maximum allowed retries for this job
    retry_class = Column(String(32), nullable=True)  # BE-W5-048: retry taxonomy class
    last_retried_at = Column(DateTime, nullable=True)  # When the job was last retried
    # BE-W5-048: dead-letter metadata
    dead_letter_reason = Column(Text, nullable=True)
    dead_letter_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    # BE-W5-054: poison-message quarantine columns
    payload_hash = Column(String(64), nullable=True, index=True)
    quarantine_reason = Column(Text, nullable=True)
    quarantined_at = Column(DateTime, nullable=True)
    # BE-W5-047: lease heartbeat for stuck-task reclamation
    worker_id = Column(String(128), nullable=True, index=True)
    heartbeat_at = Column(DateTime, nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    # BE-W5-052: retention-tier protection flags
    under_investigation = Column(Boolean, default=False, nullable=False, index=True)
    under_dispute = Column(Boolean, default=False, nullable=False, index=True)
    # BE-W5-052: audit-critical flag (retention tier)
    audit_critical = Column(Boolean, default=False, nullable=False)
