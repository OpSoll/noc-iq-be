"""merge multiple alembic heads

Revision ID: 2e4c265295a8
Revises: 0031_webhook_custom_headers, 0032_payment_dead_letter_queue, 370c52c46c2d, ec7e1a7f8c60
Create Date: 2026-09-30 11:01:32.299792

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2e4c265295a8'
down_revision: Union[str, None] = ('0031_webhook_custom_headers', '0032_payment_dead_letter_queue', '370c52c46c2d', 'ec7e1a7f8c60')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ---------------------------------------------------------------------------
# ENUM SAFETY GUARD (Issue #517)
# ---------------------------------------------------------------------------
# When defining new PostgreSQL ENUM types in this migration:
#   1. Use ``create_type=False`` on every ``postgresql.ENUM(...)`` definition
#      to prevent Alembic from auto-creating the type during table creation.
#   2. Create the type explicitly via ``<enum>.create(bind, checkfirst=True)``
#      inside an ``if bind.dialect.name == "postgresql":`` guard so the
#      migration stays compatible with SQLite (test databases).
#   3. Use ``ALTER TYPE <name> ADD VALUE IF NOT EXISTS '...'`` inside
#      ``op.get_context().autocommit_block()`` when extending existing enums —
#      PostgreSQL requires ADD VALUE outside a transaction.
# See alembic/versions/0003_operational_tables.py for the canonical example.
# ---------------------------------------------------------------------------


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
