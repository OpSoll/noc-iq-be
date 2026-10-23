"""add ondelete cascade to dispute foreign keys

Revision ID: 0026_dispute_fk_cascade
Revises: 0025_outage_soft_delete
Create Date: 2026-08-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0026_dispute_fk_cascade"
down_revision: Union[str, None] = "0025_outage_soft_delete"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Issue #523: deleting a parent outage record must cascade through
    # sla_results -> sla_disputes -> dispute_audit_logs so child dispute
    # audit logs do not block the deletion.
    op.drop_constraint(
        "sla_disputes_sla_result_id_fkey",
        "sla_disputes",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "sla_disputes_sla_result_id_fkey",
        "sla_disputes",
        "sla_results",
        ["sla_result_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "dispute_audit_logs_dispute_id_fkey",
        "dispute_audit_logs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "dispute_audit_logs_dispute_id_fkey",
        "dispute_audit_logs",
        "sla_disputes",
        ["dispute_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "dispute_audit_logs_dispute_id_fkey",
        "dispute_audit_logs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "dispute_audit_logs_dispute_id_fkey",
        "dispute_audit_logs",
        "sla_disputes",
        ["dispute_id"],
        ["id"],
    )
    op.drop_constraint(
        "sla_disputes_sla_result_id_fkey",
        "sla_disputes",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "sla_disputes_sla_result_id_fkey",
        "sla_disputes",
        "sla_results",
        ["sla_result_id"],
        ["id"],
    )
