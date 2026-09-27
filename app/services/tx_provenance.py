from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.orm import Session

from app.db.base import Base

logger = logging.getLogger(__name__)


class TxStatus(str, Enum):
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    VERIFIED = "verified"
    FAILED = "failed"


@dataclass
class TxProvenance:
    tx_hash: str
    network: str
    submitted_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    verified_at: Optional[datetime] = None
    status: str = TxStatus.SUBMITTED
    block_number: Optional[int] = None


class TxProvenanceORM(Base):
    __tablename__ = "tx_provenance"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tx_hash = Column(String(255), nullable=False, unique=True, index=True)
    network = Column(String(50), nullable=False)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(50), nullable=False, default=TxStatus.SUBMITTED, index=True)
    block_number = Column(Integer, nullable=True)
    verification_notes = Column(Text, nullable=True)


class TxProvenanceService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def record_submission(
        self,
        tx_hash: str,
        network: str,
        block_number: Optional[int] = None,
    ) -> TxProvenance:
        now = datetime.now(timezone.utc)
        existing = (
            self._db.query(TxProvenanceORM)
            .filter(TxProvenanceORM.tx_hash == tx_hash)
            .first()
        )
        if existing:
            existing.status = TxStatus.SUBMITTED
            existing.submitted_at = now
            existing.network = network
            existing.block_number = block_number
            self._db.commit()
            self._db.refresh(existing)
            return _to_dataclass(existing)

        record = TxProvenanceORM(
            tx_hash=tx_hash,
            network=network,
            submitted_at=now,
            status=TxStatus.SUBMITTED,
            block_number=block_number,
        )
        self._db.add(record)
        self._db.commit()
        self._db.refresh(record)
        logger.info("Recorded tx submission: %s on %s", tx_hash, network)
        return _to_dataclass(record)

    def confirm(
        self,
        tx_hash: str,
        block_number: Optional[int] = None,
    ) -> TxProvenance:
        record = self._get_record(tx_hash)
        now = datetime.now(timezone.utc)
        record.status = TxStatus.CONFIRMED
        record.confirmed_at = now
        if block_number is not None:
            record.block_number = block_number
        self._db.commit()
        self._db.refresh(record)
        logger.info("Confirmed tx: %s", tx_hash)
        return _to_dataclass(record)

    def verify(self, tx_hash: str) -> TxProvenance:
        record = self._get_record(tx_hash)
        now = datetime.now(timezone.utc)
        record.status = TxStatus.VERIFIED
        record.verified_at = now
        record.verification_notes = "On-chain verification completed"
        self._db.commit()
        self._db.refresh(record)
        logger.info("Verified tx: %s", tx_hash)
        return _to_dataclass(record)

    def get_status(self, tx_hash: str) -> Optional[TxProvenance]:
        record = (
            self._db.query(TxProvenanceORM)
            .filter(TxProvenanceORM.tx_hash == tx_hash)
            .first()
        )
        if not record:
            return None
        return _to_dataclass(record)

    def _get_record(self, tx_hash: str) -> TxProvenanceORM:
        record = (
            self._db.query(TxProvenanceORM)
            .filter(TxProvenanceORM.tx_hash == tx_hash)
            .first()
        )
        if not record:
            raise ValueError(f"Transaction provenance not found for hash: {tx_hash}")
        return record


def _to_dataclass(record: TxProvenanceORM) -> TxProvenance:
    return TxProvenance(
        tx_hash=record.tx_hash,
        network=record.network,
        submitted_at=record.submitted_at,
        confirmed_at=record.confirmed_at,
        verified_at=record.verified_at,
        status=record.status,
        block_number=record.block_number,
    )
