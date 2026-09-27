"""create audit_logs table (missing from migration chain)

The audit_logs table is referenced by ALTER/index statements in 0015
audit_correlation but was never created by any migration -- it previously
existed only via Base.metadata.create_all() in test fixtures. Create it
here so `alembic upgrade head` succeeds on a fresh database.

The actor_id and correlation_id columns are intentionally omitted; 0015
adds them.

Revision ID: 0014_audit_logs_table
Revises: 0014_job_retry_tracking
Create Date: 2026-08-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_audit_logs_table"
down_revision: Union[str, None] = "0014_job_retry_tracking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("audit_logs"):
        return

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_id", "audit_logs", ["id"])
    op.create_index("ix_audit_logs_event_type", "audit_logs", ["event_type"])
    op.create_index("ix_audit_logs_email", "audit_logs", ["email"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_email", table_name="audit_logs")
    op.drop_index("ix_audit_logs_event_type", table_name="audit_logs")
    op.drop_index("ix_audit_logs_id", table_name="audit_logs")
    op.drop_table("audit_logs")
