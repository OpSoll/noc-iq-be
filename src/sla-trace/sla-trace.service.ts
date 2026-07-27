import { SlaTraceRepository } from './sla-trace.repository';
import { SlaCalculationTrace, CreateSlaTraceDto } from './sla-trace.model';

export interface SlaCalculationInput {
  incident_id: string;
  severity: string;
  threshold_minutes: number;
  resolved_at: Date | null;
  opened_at: Date;
}

export interface SlaCalculationResult {
  mttr_minutes: number;
  decision_branch: string;
  sla_breached: boolean;
  trace: SlaCalculationTrace;
}

export class SlaTraceService {
  constructor(private readonly repo: SlaTraceRepository) {}

  async calculateAndTrace(input: SlaCalculationInput): Promise<SlaCalculationResult> {
    const { incident_id, severity, threshold_minutes, resolved_at, opened_at } = input;

    let mttr_minutes = 0;
    let decision_branch: string;
    let sla_breached: boolean;

    // Decision logic with full trace of each branch
    if (!resolved_at) {
      const now = new Date();
      mttr_minutes = (now.getTime() - opened_at.getTime()) / 60000;
      decision_branch = 'UNRESOLVED_ELAPSED_TIME';
      sla_breached = mttr_minutes > threshold_minutes;
    } else {
      mttr_minutes = (resolved_at.getTime() - opened_at.getTime()) / 60000;

      if (mttr_minutes <= 0) {
        decision_branch = 'INVALID_NEGATIVE_MTTR';
        sla_breached = false;
      } else if (mttr_minutes <= threshold_minutes) {
        decision_branch = 'WITHIN_SLA_THRESHOLD';
        sla_breached = false;
      } else {
        decision_branch = 'EXCEEDED_SLA_THRESHOLD';
        sla_breached = true;
      }
    }

    const trace_payload: Record<string, unknown> = {
      inputs: {
        incident_id,
        severity,
        threshold_minutes,
        opened_at: opened_at.toISOString(),
        resolved_at: resolved_at ? resolved_at.toISOString() : null,
      },
      computation: {
        mttr_minutes: parseFloat(mttr_minutes.toFixed(2)),
        threshold_minutes,
        difference_minutes: parseFloat((mttr_minutes - threshold_minutes).toFixed(2)),
      },
      decision: {
        branch: decision_branch,
        sla_breached,
        evaluated_at: new Date().toISOString(),
      },
    };

    const dto: CreateSlaTraceDto = {
      incident_id,
      severity,
      threshold_minutes,
      mttr_minutes: parseFloat(mttr_minutes.toFixed(2)),
      decision_branch,
      sla_breached,
      trace_payload,
    };

    const trace = await this.repo.create(dto);

    return { mttr_minutes, decision_branch, sla_breached, trace };
  }

  async getTracesByIncident(incidentId: string): Promise<SlaCalculationTrace[]> {
    return this.repo.findByIncidentId(incidentId);
  }

  async getTraceById(id: string): Promise<SlaCalculationTrace | null> {
    return this.repo.findById(id);
  }

  async getAllTraces(limit?: number, offset?: number): Promise<SlaCalculationTrace[]> {
    return this.repo.findAll(limit, offset);
  }
}
