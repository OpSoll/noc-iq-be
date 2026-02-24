import { SlaConfigHistoryRepository } from './sla-config-history.repository';
import { SlaConfigVersion } from './sla-config-history.model';

type ConfigMap = Record<string, unknown>;

export interface RecordConfigChangeInput {
  config_id: string;
  changed_by: string;
  new_config: ConfigMap;
  change_reason?: string;
}

function computeDiff(
  previous: ConfigMap,
  next: ConfigMap
): Record<string, { from: unknown; to: unknown }> {
  const diff: Record<string, { from: unknown; to: unknown }> = {};
  const allKeys = new Set([...Object.keys(previous), ...Object.keys(next)]);

  for (const key of allKeys) {
    const prev = previous[key];
    const curr = next[key];

    const changed =
      JSON.stringify(prev) !== JSON.stringify(curr);

    if (changed) {
      diff[key] = { from: prev ?? null, to: curr ?? null };
    }
  }

  return diff;
}

export class SlaConfigHistoryService {
  constructor(private readonly repo: SlaConfigHistoryRepository) {}

  async recordChange(input: RecordConfigChangeInput): Promise<SlaConfigVersion> {
    const { config_id, changed_by, new_config, change_reason } = input;

    const latest = await this.repo.findLatest(config_id);
    const previousSnapshot: ConfigMap = latest?.config_snapshot ?? {};
    const diff = computeDiff(previousSnapshot, new_config);

    return this.repo.create({
      config_id,
      changed_by,
      config_snapshot: new_config,
      diff,
      change_reason,
    });
  }

  async getHistory(configId: string): Promise<SlaConfigVersion[]> {
    return this.repo.findByConfigId(configId);
  }

  async getVersion(configId: string, version: number): Promise<SlaConfigVersion | null> {
    return this.repo.findByVersion(configId, version);
  }

  async getLatest(configId: string): Promise<SlaConfigVersion | null> {
    return this.repo.findLatest(configId);
  }

  async getChangesByUser(changedBy: string): Promise<SlaConfigVersion[]> {
    return this.repo.findByChangedBy(changedBy);
  }

  async getAllHistory(limit?: number, offset?: number): Promise<SlaConfigVersion[]> {
    return this.repo.findAll(limit, offset);
  }
}
