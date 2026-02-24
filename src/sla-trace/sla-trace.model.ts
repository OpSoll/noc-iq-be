export interface SlaCalculationTrace {
  id: string;
  incident_id: string;
  severity: string;
  threshold_minutes: number;
  mttr_minutes: number;
  decision_branch: string;
  sla_breached: boolean;
  trace_payload: Record<string, unknown>;
  created_at: Date;
}

export interface CreateSlaTraceDto {
  incident_id: string;
  severity: string;
  threshold_minutes: number;
  mttr_minutes: number;
  decision_branch: string;
  sla_breached: boolean;
  trace_payload: Record<string, unknown>;
}
