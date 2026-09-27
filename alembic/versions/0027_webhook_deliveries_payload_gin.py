"""Convert webhook_deliveries.payload to JSONB and add a GIN index.

Revision ID: 0027_webhook_deliveries_payload_gin
Revises: 990e5bdf29fa
Create Date: 2026-08-27

Issue #519: searching JSON payload content in ``webhook_deliveries``
required sequential scans over text columns. This migration converts the
``payload`` column to ``JSONB`` (PostgreSQL only) and creates a GIN index so
JSON containment queries (``payload @> ...``) use the index.

The ORM keeps binding ``payload`` as a JSON string, so webhook signing and
delivery behaviour is unchanged.
"""
from alembic import op
import sqlalchemy as sa  # noqa: F401


revision = "0027_webhook_deliveries_payload_gin"
down_revision = "0026_celery_task_dead_letters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # PostgreSQL-specific conversion; other dialects keep the Text column.
        return

    # Normalise any non-JSON legacy rows first (empty / invalid text becomes
    # an empty JSON object), then cast. The temporary plpgsql validator is
    # portable across PostgreSQL versions (json_valid() is PG 17+ only).
    op.execute(
        "CREATE OR REPLACE FUNCTION _nociq_is_json(payload text) "
        "RETURNS boolean AS $$ "
        "BEGIN "
        "    IF payload IS NULL OR payload = '' THEN "
        "        RETURN false; "
        "    END IF; "
        "    BEGIN "
        "        PERFORM payload::jsonb; "
        "        RETURN true; "
        "    EXCEPTION WHEN others THEN "
        "        RETURN false; "
        "    END; "
        "END; $$ LANGUAGE plpgsql"
    )
    try:
        op.execute(
            "UPDATE webhook_deliveries "
            "SET payload = '{}' "
            "WHERE NOT _nociq_is_json(payload)"
        )
        op.execute(
            "ALTER TABLE webhook_deliveries "
            "ALTER COLUMN payload TYPE JSONB USING payload::jsonb"
        )
    finally:
        op.execute("DROP FUNCTION IF EXISTS _nociq_is_json(text)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_payload_gin "
        "ON webhook_deliveries USING GIN (payload)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("DROP INDEX IF EXISTS ix_webhook_deliveries_payload_gin")
    op.execute(
        "ALTER TABLE webhook_deliveries "
        "ALTER COLUMN payload TYPE TEXT USING payload::text"
    )
