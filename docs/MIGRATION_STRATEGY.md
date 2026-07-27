# Zero-Downtime Database Migration Strategy

## Overview

This document describes the zero-downtime migration approach used in the
NOC IQ backend, built around the **shadow-table pattern** and Alembic
migration scripts.

## Shadow-Table Pattern

The shadow-table pattern eliminates downtime during schema changes by
running the old and new table side-by-side:

```
1. create_shadow_table(original)
   - Creates `<original>_shadow` with the NEW schema
2. migrate_data(source=original, target=shadow)
   - Batched copy of all existing rows
3. switch_read_traffic(original, shadow)
   - Original table is renamed to `<original>_view_backup`
   - A view named `<original>` now reads from the shadow table
   - Application reads are transparently redirected
4. cleanup_shadow(original)
   - Drops the shadow table and backup view
   - Renames the backup back to the original name
```

### Why This Works

- **No lock contention**: The original table remains fully available for
  reads and writes during the migration.
- **Rollback ready**: At any point the migration can be rolled back by
  reverting the view rename.
- **Batched copy**: Large tables are migrated in configurable batches
  (`MIGRATION_BATCH_SIZE`, default 500 rows) to avoid memory spikes.

## Alembic Integration

Alembic is used for forward-only schema migrations. The shadow-table
utility is used only for major rewrites that cannot be expressed as
simple `ALTER TABLE` statements.

### Typical Alembic Workflow

```bash
# Generate a new migration
alembic revision --autogenerate -m "add_new_column"

# Apply pending migrations
alembic upgrade head

# Roll back one revision
alembic downgrade -1
```

### Using Shadow Tables in Alembic

```python
from app.migrations.shadow_table import (
    create_shadow_table,
    migrate_data,
    switch_read_traffic,
    cleanup_shadow,
)

def upgrade():
    # Step 1: Create shadow with new schema
    shadow = create_shadow_table(engine, "sla_snapshots")
    # Step 2: Migrate existing data
    migrate_data(engine, "sla_snapshots", shadow)
    # Step 3: Switch reads
    switch_read_traffic(engine, "sla_snapshots", shadow)

def downgrade():
    cleanup_shadow(engine, "sla_snapshots")
```

## Configuration

| Setting                      | Default | Description                          |
|------------------------------|---------|--------------------------------------|
| `MIGRATION_BATCH_SIZE`       | 500     | Rows per batch during data migration |
| `MIGRATION_SHADOW_SUFFIX`    | `_shadow` | Suffix appended to shadow tables   |

## Safety Checklist

- [ ] Run migration on staging first
- [ ] Verify row counts match between source and shadow
- [ ] Confirm application health checks pass after switch
- [ ] Monitor error rates for 15 minutes post-switch
- [ ] Keep backup tables for 7 days before final cleanup
