"""add composite index on outages (site_id, status, detected_at)

Revision ID: 0025_add_outages_site_status_idx
Revises: 990e5bdf29fa
Create Date: 2026-08-19

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0025_add_outages_site_status_idx"
down_revision: Union[str, None] = "990e5bdf29fa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Issue #515: queries filtering open outages by site ID benefit from a
    # composite index on (site_id, status, detected_at) instead of full scans.
    op.create_index(
        "ix_outages_site_status_detected",
        "outages",
        ["site_id", "status", "detected_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_outages_site_status_detected", table_name="outages")
