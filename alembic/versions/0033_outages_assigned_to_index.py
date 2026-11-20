"""add index on outages.assigned_to

Revision ID: 0033_outages_assigned_to_index
Revises: 2e4c265295a8
Create Date: 2026-11-20

"""
from alembic import op

revision = "0033_outages_assigned_to_index"
down_revision = "2e4c265295a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_outages_assigned_to", "outages", ["assigned_to"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_outages_assigned_to", table_name="outages", if_exists=True)
