from typing import Optional
from sqlalchemy.orm import Session
from app.models.orm.user import UserORM
from app.models.auth import AuthUser

def user_orm_to_pydantic(orm: UserORM) -> AuthUser:
    return AuthUser(
        id=orm.id,
        email=orm.email,
        full_name=orm.full_name,
        role=orm.role,
        created_at=orm.created_at,
    )

class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_email(self, email: str) -> Optional[UserORM]:
        return self.db.query(UserORM).filter(UserORM.email == email).first()

    def get_by_id(self, user_id: str) -> Optional[UserORM]:
        return self.db.query(UserORM).filter(UserORM.id == user_id).first()

    def create(self, user_id: str, email: str, hashed_password: str, full_name: str, role: str) -> UserORM:
        user = UserORM(
            id=user_id,
            email=email,
            hashed_password=hashed_password,
            full_name=full_name,
            role=role
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user
