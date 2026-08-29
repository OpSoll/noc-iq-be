import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base_class import Base


class APIKey(Base):
    __tablename__ = "api_keys"

    id = sa.Column(sa.Integer, primary_key=True, index=True)
    key = sa.Column(sa.String, unique=True, index=True, nullable=False)
    scopes = sa.Column(JSONB, nullable=False)
    is_active = sa.Column(sa.Boolean, default=True)
    created_at = sa.Column(sa.DateTime, default=sa.func.now(), nullable=False)
    updated_at = sa.Column(sa.DateTime, default=sa.func.now(), onupdate=sa.func.now(), nullable=False)