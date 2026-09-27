from sqlalchemy.orm import Session
from app.models.idempotency import IdempotencyKey
from app.schemas.idempotency import IdempotencyKeyCreate

class IdempotencyService:
    def process_key(self, db: Session, key_in: IdempotencyKeyCreate) -> IdempotencyKey:
        existing = db.query(IdempotencyKey).filter(IdempotencyKey.key == key_in.key).first()
        if existing:
            return existing

        db_key = IdempotencyKey(key=key_in.key, endpoint=key_in.endpoint)
        db.add(db_key)
        db.commit()
        db.refresh(db_key)
        return db_key
