"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}

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
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
