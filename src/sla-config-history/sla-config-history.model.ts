export interface SlaConfigVersion {
  id: string;
  config_id: string;
  version: number;
  changed_by: string;
  changed_at: Date;
  config_snapshot: Record<string, unknown>;
  diff: Record<string, unknown>;
  change_reason: string | null;
}

export interface CreateSlaConfigVersionDto {
  config_id: string;
  changed_by: string;
  config_snapshot: Record<string, unknown>;
  diff: Record<string, unknown>;
  change_reason?: string;
}
