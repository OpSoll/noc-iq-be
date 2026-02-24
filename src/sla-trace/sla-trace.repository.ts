import { Pool } from 'pg';
import { SlaCalculationTrace, CreateSlaTraceDto } from './sla-trace.model';

export class SlaTraceRepository {
  constructor(private readonly pool: Pool) {}

  async create(dto: CreateSlaTraceDto): Promise<SlaCalculationTrace> {
    const { rows } = await this.pool.query<SlaCalculationTrace>(
      `INSERT INTO sla_calculation_traces
        (incident_id, severity, threshold_minutes, mttr_minutes, decision_branch, sla_breached, trace_payload)
       VALUES ($1, $2, $3, $4, $5, $6, $7)
       RETURNING *`,
      [
        dto.incident_id,
        dto.severity,
        dto.threshold_minutes,
        dto.mttr_minutes,
        dto.decision_branch,
        dto.sla_breached,
        JSON.stringify(dto.trace_payload),
      ]
    );
    return rows[0];
  }

  async findByIncidentId(incidentId: string): Promise<SlaCalculationTrace[]> {
    const { rows } = await this.pool.query<SlaCalculationTrace>(
      `SELECT * FROM sla_calculation_traces
       WHERE incident_id = $1
       ORDER BY created_at DESC`,
      [incidentId]
    );
    return rows;
  }

  async findById(id: string): Promise<SlaCalculationTrace | null> {
    const { rows } = await this.pool.query<SlaCalculationTrace>(
      `SELECT * FROM sla_calculation_traces WHERE id = $1`,
      [id]
    );
    return rows[0] ?? null;
  }

  async findAll(limit = 50, offset = 0): Promise<SlaCalculationTrace[]> {
    const { rows } = await this.pool.query<SlaCalculationTrace>(
      `SELECT * FROM sla_calculation_traces
       ORDER BY created_at DESC
       LIMIT $1 OFFSET $2`,
      [limit, offset]
    );
    return rows;
  }
}
