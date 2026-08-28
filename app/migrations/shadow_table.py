from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import Table, MetaData, select, text
from sqlalchemy.engine import Engine

from app.core.config import settings

logger = logging.getLogger(__name__)


def _shadow_name(original_table: str) -> str:
    return f"{original_table}{settings.MIGRATION_SHADOW_SUFFIX}"


def create_shadow_table(engine: Engine, original_table_name: str) -> str:
    """Create a shadow table as an exact copy of the original table schema.

    Returns the shadow table name.
    """
    metadata = MetaData()
    metadata.reflect(bind=engine, only=[original_table_name])
    original = metadata.tables[original_table_name]

    shadow_name = _shadow_name(original_table_name)
    shadow = Table(
        shadow_name,
        metadata,
        *(col.copy() for col in original.columns),
        extend_existing=True,
    )
    shadow.create(bind=engine, checkfirst=True)
    logger.info(
        "Shadow table created | original=%s shadow=%s",
        original_table_name,
        shadow_name,
    )
    return shadow_name


def migrate_data(
    engine: Engine,
    source_table_name: str,
    target_table_name: str,
    batch_size: Optional[int] = None,
) -> int:
    """Copy rows from source to target table in batches.

    Returns the total number of rows migrated.
    """
    batch_size = batch_size or settings.MIGRATION_BATCH_SIZE
    metadata = MetaData()
    metadata.reflect(bind=engine, only=[source_table_name, target_table_name])
    source = metadata.tables[source_table_name]
    target = metadata.tables[target_table_name]

    total = 0
    with engine.begin() as conn:
        while True:
            rows = conn.execute(
                select(source).limit(batch_size).offset(total)
            ).fetchall()
            if not rows:
                break
            conn.execute(target.insert(), [dict(row._mapping) for row in rows])
            total += len(rows)
            logger.info(
                "Migration batch migrated | source=%s target=%s batch=%d total=%d",
                source_table_name,
                target_table_name,
                len(rows),
                total,
            )

    logger.info(
        "Migration complete | source=%s target=%s total_rows=%d",
        source_table_name,
        target_table_name,
        total,
    )
    return total


def switch_read_traffic(
    engine: Engine,
    table_name: str,
    shadow_name: Optional[str] = None,
) -> None:
    """Switch application reads from the original table to the shadow table.

    This creates a database view with the original table name that points to
    the shadow table, transparently redirecting reads.
    """
    shadow = shadow_name or _shadow_name(table_name)
    with engine.begin() as conn:
        conn.execute(text(f'DROP VIEW IF EXISTS "{table_name}_view_backup"'))
        conn.execute(text(f'ALTER TABLE "{table_name}" RENAME TO "{table_name}_view_backup"'))
        conn.execute(
            text(
                f'CREATE VIEW "{table_name}" AS SELECT * FROM "{shadow}"'
            )
        )
    logger.info(
        "Read traffic switched | original=%s shadow=%s",
        table_name,
        shadow,
    )


def cleanup_shadow(engine: Engine, table_name: str) -> None:
    """Drop the shadow table and backup view after verification."""
    shadow = _shadow_name(table_name)
    with engine.begin() as conn:
        conn.execute(text(f'DROP VIEW IF EXISTS "{table_name}"'))
        conn.execute(text(f'ALTER TABLE "{table_name}_view_backup" RENAME TO "{table_name}"'))
        conn.execute(text(f'DROP TABLE IF EXISTS "{shadow}"'))
    logger.info(
        "Shadow cleanup complete | table=%s shadow=%s",
        table_name,
        shadow,
    )
