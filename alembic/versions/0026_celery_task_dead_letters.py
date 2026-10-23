"""Add celery task dead-letter audit table.

Revision ID: 0026_celery_task_dead_letters
Revises: 0025_payment_tx_to_addr_status_index
Create Date: 2026-08-27

Issue #530: Celery tasks that fail permanently due to unhandled worker
exceptions are routed to the ``celery_dead_letter`` queue. This table
stores the failed task payload and traceback for audit and replay.
"""
from alembic import op
import sqlalchemy as sa


revision = "0026_celery_task_dead_letters"
down_revision = "0025_payment_tx_to_addr_status_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "celery_task_dead_letters",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("task_id", sa.String(length=255), nullable=False),
        sa.Column("task_name", sa.String(length=255), nullable=False),
        sa.Column(
            "queue",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'celery_dead_letter'"),
        ),
        sa.Column("args_json", sa.Text(), nullable=True),
        sa.Column("kwargs_json", sa.Text(), nullable=True),
        sa.Column("exception", sa.Text(), nullable=True),
        sa.Column("traceback", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_celery_task_dead_letters_task_id",
        "celery_task_dead_letters",
        ["task_id"],
        unique=False,
    )
    op.create_index(
        "ix_celery_task_dead_letters_created_at",
        "celery_task_dead_letters",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_celery_task_dead_letters_created_at",
        table_name="celery_task_dead_letters",
    )
    op.drop_index(
        "ix_celery_task_dead_letters_task_id",
        table_name="celery_task_dead_letters",
    )
    op.drop_table("celery_task_dead_letters")
