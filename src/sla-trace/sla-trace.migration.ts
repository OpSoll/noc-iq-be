import { Pool } from 'pg';

export async function runSlaTraceMigration(pool: Pool): Promise<void> {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS sla_calculation_traces (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      incident_id VARCHAR(255) NOT NULL,
      severity VARCHAR(100) NOT NULL,
      threshold_minutes NUMERIC(10, 2) NOT NULL,
      mttr_minutes NUMERIC(10, 2) NOT NULL,
      decision_branch VARCHAR(255) NOT NULL,
      sla_breached BOOLEAN NOT NULL DEFAULT FALSE,
      trace_payload JSONB NOT NULL DEFAULT '{}',
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_sla_traces_incident_id
      ON sla_calculation_traces (incident_id);

    CREATE INDEX IF NOT EXISTS idx_sla_traces_created_at
      ON sla_calculation_traces (created_at DESC);
  `);
}
