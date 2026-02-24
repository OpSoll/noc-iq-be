import { Pool } from 'pg';

export async function runSlaConfigHistoryMigration(pool: Pool): Promise<void> {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS sla_config_versions (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      config_id VARCHAR(255) NOT NULL,
      version INTEGER NOT NULL,
      changed_by VARCHAR(255) NOT NULL,
      changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      config_snapshot JSONB NOT NULL DEFAULT '{}',
      diff JSONB NOT NULL DEFAULT '{}',
      change_reason TEXT,
      UNIQUE (config_id, version)
    );

    CREATE INDEX IF NOT EXISTS idx_sla_config_versions_config_id
      ON sla_config_versions (config_id);

    CREATE INDEX IF NOT EXISTS idx_sla_config_versions_changed_at
      ON sla_config_versions (changed_at DESC);

    CREATE INDEX IF NOT EXISTS idx_sla_config_versions_changed_by
      ON sla_config_versions (changed_by);
  `);
}
