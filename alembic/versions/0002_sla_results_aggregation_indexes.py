"""add indexes for sla aggregation

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-25

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_sla_results_created_at", "sla_results", ["created_at"])
    op.create_index(
        "ix_sla_results_outage_created_at",
        "sla_results",
        ["outage_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_sla_results_outage_created_at", table_name="sla_results")
    op.drop_index("ix_sla_results_created_at", table_name="sla_results")
