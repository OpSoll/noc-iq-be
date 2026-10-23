"""merge multiple alembic heads

Revision ID: 990e5bdf29fa
Revises: 0017_payment_idempotency_key, 0018_payment_dead_letter_queue, 0024_mercy60_job_enhancements
Create Date: 2026-08-13 11:24:23.175034

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '990e5bdf29fa'
down_revision: Union[str, None] = ('0017_payment_idempotency_key', '0018_payment_dead_letter_queue', '0024_mercy60_job_enhancements')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
