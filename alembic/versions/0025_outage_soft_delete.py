"""add soft-delete columns to outages

Revision ID: 0025_outage_soft_delete
Revises: 990e5bdf29fa
Create Date: 2026-08-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0025_outage_soft_delete"
down_revision: Union[str, None] = "990e5bdf29fa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Issue #521: soft-delete flag for SLA outage records.
    # Existing rows are treated as not deleted via the server default.
    op.add_column(
        "outages",
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "outages",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_outages_is_deleted", "outages", ["is_deleted"])


def downgrade() -> None:
    op.drop_index("ix_outages_is_deleted", table_name="outages")
    op.drop_column("outages", "deleted_at")
    op.drop_column("outages", "is_deleted")
