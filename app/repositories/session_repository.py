from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from app.models.orm.session import SessionORM

class SessionRepository:
    def __init__(self, db: Session):
        self.db = db

    def create_session(self, access_token: str, refresh_token: str, email: str, expires_at: datetime) -> SessionORM:
        session = SessionORM(
            access_token=access_token,
            refresh_token=refresh_token,
            email=email,
            expires_at=expires_at
        )
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def get_session(self, access_token: str) -> Optional[SessionORM]:
        return self.db.query(SessionORM).filter(SessionORM.access_token == access_token).first()

    def get_session_by_refresh_token(self, refresh_token: str) -> Optional[SessionORM]:
        return self.db.query(SessionORM).filter(SessionORM.refresh_token == refresh_token).first()

    def delete_session(self, access_token: str) -> None:
        session = self.get_session(access_token)
        if session:
            self.db.delete(session)
            self.db.commit()
