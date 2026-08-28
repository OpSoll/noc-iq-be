"""BE-W5-047/048/050/052: Job enhancements for Mercy60 issues.

Revision ID: 0024_mercy60_job_enhancements
Revises: 0023_job_quarantine_and_dr_replay
Create Date: 2026-07-29

Adds:
  - BE-W5-050: ``error_code``, ``error_retryable``, ``error_details`` columns
  - BE-W5-048: ``retry_class``, ``dead_letter_reason``, ``dead_letter_at`` columns
  - BE-W5-047: ``worker_id``, ``heartbeat_at``, ``lease_expires_at`` columns
  - BE-W5-052: ``under_investigation``, ``under_dispute``, ``audit_critical`` columns
  - Extends ``jobstatus`` enum with ``dead_letter`` value
"""
from alembic import op
import sqlalchemy as sa


revision = "0024_mercy60_job_enhancements"
down_revision = "0023_job_quarantine_and_dr_replay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # BE-W5-050: Typed error metadata
    op.add_column(
        "jobs",
        sa.Column("error_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("error_retryable", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("error_details", sa.JSON(), nullable=True),
    )
    op.create_index(
        "idx_jobs_error_code",
        "jobs",
        ["error_code"],
        unique=False,
    )

    # BE-W5-048: Retry taxonomy and dead-letter
    op.add_column(
        "jobs",
        sa.Column("retry_class", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("dead_letter_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("dead_letter_at", sa.DateTime(), nullable=True),
    )

    # Extend jobstatus enum with DEAD_LETTER
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS 'dead_letter'")

    # BE-W5-047: Lease heartbeat
    op.add_column(
        "jobs",
        sa.Column("worker_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "idx_jobs_worker_id",
        "jobs",
        ["worker_id"],
        unique=False,
    )
    op.create_index(
        "idx_jobs_lease_expires_at",
        "jobs",
        ["lease_expires_at"],
        unique=False,
    )

    # BE-W5-052: Retention tier protection flags
    op.add_column(
        "jobs",
        sa.Column(
            "under_investigation",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "under_dispute",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "audit_critical",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "idx_jobs_under_investigation",
        "jobs",
        ["under_investigation"],
        unique=False,
    )
    op.create_index(
        "idx_jobs_under_dispute",
        "jobs",
        ["under_dispute"],
        unique=False,
    )


def downgrade() -> None:
    # BE-W5-052
    op.drop_index("idx_jobs_under_dispute", table_name="jobs")
    op.drop_index("idx_jobs_under_investigation", table_name="jobs")
    op.drop_column("jobs", "audit_critical")
    op.drop_column("jobs", "under_dispute")
    op.drop_column("jobs", "under_investigation")

    # BE-W5-047
    op.drop_index("idx_jobs_lease_expires_at", table_name="jobs")
    op.drop_index("idx_jobs_worker_id", table_name="jobs")
    op.drop_column("jobs", "lease_expires_at")
    op.drop_column("jobs", "heartbeat_at")
    op.drop_column("jobs", "worker_id")

    # BE-W5-048 (enum value not removable)
    op.drop_column("jobs", "dead_letter_at")
    op.drop_column("jobs", "dead_letter_reason")
    op.drop_column("jobs", "retry_class")

    # BE-W5-050
    op.drop_index("idx_jobs_error_code", table_name="jobs")
    op.drop_column("jobs", "error_details")
    op.drop_column("jobs", "error_retryable")
    op.drop_column("jobs", "error_code")
