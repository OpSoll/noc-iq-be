"""create sessions table (missing from migration chain)

The sessions table is referenced by ALTER/index/FK statements in 0009
token_families but was never created by any migration -- it previously
existed only via Base.metadata.create_all() in test fixtures. Create it
here so `alembic upgrade head` succeeds on a fresh database.

The family_id and sequence columns are intentionally omitted; 0009 adds
them along with the token_families foreign key.

Revision ID: 0008_sessions_table
Revises: 0008
Create Date: 2026-08-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_sessions_table"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("sessions"):
        return

    op.create_table(
        "sessions",
        sa.Column("access_token", sa.String(255), nullable=False),
        sa.Column("refresh_token", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["email"], ["users.email"]),
        sa.PrimaryKeyConstraint("access_token"),
        sa.UniqueConstraint("refresh_token"),
    )
    op.create_index("ix_sessions_access_token", "sessions", ["access_token"])
    op.create_index("ix_sessions_refresh_token", "sessions", ["refresh_token"])


def downgrade() -> None:
    op.drop_index("ix_sessions_refresh_token", table_name="sessions")
    op.drop_index("ix_sessions_access_token", table_name="sessions")
    op.drop_table("sessions")
