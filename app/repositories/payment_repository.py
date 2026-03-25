from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.orm.payment import PaymentTransactionORM
from app.models.payment import PaymentTransaction
from app.models.sla import SLAResult


def _orm_to_pydantic(orm: PaymentTransactionORM) -> PaymentTransaction:
    return PaymentTransaction(
        id=orm.id,
        transaction_hash=orm.transaction_hash,
        type=orm.type,
        amount=orm.amount,
        asset_code=orm.asset_code,
        from_address=orm.from_address,
        to_address=orm.to_address,
        status=orm.status,
        outage_id=orm.outage_id,
        sla_result_id=orm.sla_result_id,
        created_at=orm.created_at,
        confirmed_at=orm.confirmed_at,
    )


class PaymentRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, data: PaymentTransaction) -> PaymentTransaction:
        orm = PaymentTransactionORM(
            id=data.id,
            transaction_hash=data.transaction_hash,
            type=data.type,
            amount=data.amount,
            asset_code=data.asset_code,
            from_address=data.from_address,
            to_address=data.to_address,
            status=data.status,
            outage_id=data.outage_id,
            sla_result_id=data.sla_result_id,
            created_at=data.created_at,
            confirmed_at=data.confirmed_at,
        )
        self.db.add(orm)
        self.db.commit()
        self.db.refresh(orm)
        return _orm_to_pydantic(orm)

    def get(self, transaction_id: str) -> Optional[PaymentTransaction]:
        orm = (
            self.db.query(PaymentTransactionORM)
            .filter(PaymentTransactionORM.id == transaction_id)
            .first()
        )
        if not orm:
            return None
        return _orm_to_pydantic(orm)

    def get_by_sla_result(self, sla_result_id: int) -> Optional[PaymentTransaction]:
        orm = (
            self.db.query(PaymentTransactionORM)
            .filter(PaymentTransactionORM.sla_result_id == sla_result_id)
            .first()
        )
        if not orm:
            return None
        return _orm_to_pydantic(orm)

    def list(
        self,
        page: int = 1,
        page_size: int = 20,
        status: Optional[str] = None,
        outage_id: Optional[str] = None,
    ) -> tuple[List[PaymentTransaction], int]:
        query = self.db.query(PaymentTransactionORM)

        if status:
            query = query.filter(PaymentTransactionORM.status == status)
        if outage_id:
            query = query.filter(PaymentTransactionORM.outage_id == outage_id)

        total = query.count()
        rows = (
            query.order_by(PaymentTransactionORM.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return [_orm_to_pydantic(r) for r in rows], total

    def list_by_outage(self, outage_id: str) -> List[PaymentTransaction]:
        rows = (
            self.db.query(PaymentTransactionORM)
            .filter(PaymentTransactionORM.outage_id == outage_id)
            .all()
        )
        return [_orm_to_pydantic(r) for r in rows]

    def update_status(self, transaction_id: str, status: str) -> Optional[PaymentTransaction]:
        orm = (
            self.db.query(PaymentTransactionORM)
            .filter(PaymentTransactionORM.id == transaction_id)
            .first()
        )
        if not orm:
            return None
        orm.status = status
        self.db.commit()
        self.db.refresh(orm)
        return _orm_to_pydantic(orm)

    def create_for_sla_result(self, outage_id: str, sla_result: SLAResult) -> PaymentTransaction:
        if sla_result.id is None:
            raise ValueError("SLA result id is required to generate a payment record")

        existing = self.get_by_sla_result(sla_result.id)
        if existing:
            return existing

        normalized_amount = abs(float(sla_result.amount))
        transaction = PaymentTransaction(
            id=f"pay_{uuid4().hex[:12]}",
            transaction_hash=f"sla-{sla_result.id}-{sla_result.payment_type}",
            type=sla_result.payment_type,
            amount=normalized_amount,
            asset_code="USDC",
            from_address="SYSTEM_POOL",
            to_address="OUTAGE_SETTLEMENT",
            status="pending",
            outage_id=outage_id,
            sla_result_id=sla_result.id,
            created_at=datetime.utcnow(),
            confirmed_at=None,
        )
        return self.create(transaction)
