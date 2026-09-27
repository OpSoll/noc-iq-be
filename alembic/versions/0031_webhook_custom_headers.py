"""Add custom_headers_encrypted column to webhooks table.

Stores custom HTTP headers for outgoing webhook dispatches encrypted
at rest using Fernet (AES-128-CBC + HMAC-SHA256). Headers are
base64-encoded after encryption so they fit in a TEXT column on both
PostgreSQL and SQLite.

Revision ID: 0031_webhook_custom_headers
"""
from alembic import op
import sqlalchemy as sa


revision = "0031_webhook_custom_headers"
down_revision = "0030_webhook_dead_letter_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "webhooks",
        sa.Column("custom_headers_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("webhooks", "custom_headers_encrypted")
