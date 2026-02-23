from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.orm.payment import PaymentTransactionORM
from app.models.payment import PaymentTransaction


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
