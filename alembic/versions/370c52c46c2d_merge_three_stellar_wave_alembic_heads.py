"""merge three stellar wave alembic heads

Revision ID: 370c52c46c2d
Revises: 0025_add_outages_site_status_idx, 0026_dispute_fk_cascade, 0028_webhook_deliveries_partitioning
Create Date: 2026-08-27 23:12:37.103345

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '370c52c46c2d'
down_revision: Union[str, None] = ('0025_add_outages_site_status_idx', '0026_dispute_fk_cascade', '0028_webhook_deliveries_partitioning')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
