"""merge multiple alembic heads

Revision ID: f86d8db3947f
Revises: 0025_add_outages_site_status_idx, 0026_dispute_fk_cascade, 0028_webhook_deliveries_partitioning
Create Date: 2026-08-27 23:14:37.428911

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f86d8db3947f'
down_revision: Union[str, None] = ('0025_add_outages_site_status_idx', '0026_dispute_fk_cascade', '0028_webhook_deliveries_partitioning')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
