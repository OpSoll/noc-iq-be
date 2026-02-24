import { Pool } from 'pg';
import { SlaConfigVersion, CreateSlaConfigVersionDto } from './sla-config-history.model';

export class SlaConfigHistoryRepository {
  constructor(private readonly pool: Pool) {}

  async getLatestVersion(configId: string): Promise<number> {
    const { rows } = await this.pool.query<{ version: number }>(
      `SELECT COALESCE(MAX(version), 0) AS version
       FROM sla_config_versions
       WHERE config_id = $1`,
      [configId]
    );
    return rows[0].version;
  }

  async create(dto: CreateSlaConfigVersionDto): Promise<SlaConfigVersion> {
    const nextVersion = (await this.getLatestVersion(dto.config_id)) + 1;

    const { rows } = await this.pool.query<SlaConfigVersion>(
      `INSERT INTO sla_config_versions
        (config_id, version, changed_by, config_snapshot, diff, change_reason)
       VALUES ($1, $2, $3, $4, $5, $6)
       RETURNING *`,
      [
        dto.config_id,
        nextVersion,
        dto.changed_by,
        JSON.stringify(dto.config_snapshot),
        JSON.stringify(dto.diff),
        dto.change_reason ?? null,
      ]
    );
    return rows[0];
  }

  async findByConfigId(configId: string): Promise<SlaConfigVersion[]> {
    const { rows } = await this.pool.query<SlaConfigVersion>(
      `SELECT * FROM sla_config_versions
       WHERE config_id = $1
       ORDER BY version DESC`,
      [configId]
    );
    return rows;
  }

  async findByVersion(configId: string, version: number): Promise<SlaConfigVersion | null> {
    const { rows } = await this.pool.query<SlaConfigVersion>(
      `SELECT * FROM sla_config_versions
       WHERE config_id = $1 AND version = $2`,
      [configId, version]
    );
    return rows[0] ?? null;
  }

  async findLatest(configId: string): Promise<SlaConfigVersion | null> {
    const { rows } = await this.pool.query<SlaConfigVersion>(
      `SELECT * FROM sla_config_versions
       WHERE config_id = $1
       ORDER BY version DESC
       LIMIT 1`,
      [configId]
    );
    return rows[0] ?? null;
  }

  async findByChangedBy(changedBy: string): Promise<SlaConfigVersion[]> {
    const { rows } = await this.pool.query<SlaConfigVersion>(
      `SELECT * FROM sla_config_versions
       WHERE changed_by = $1
       ORDER BY changed_at DESC`,
      [changedBy]
    );
    return rows;
  }

  async findAll(limit = 50, offset = 0): Promise<SlaConfigVersion[]> {
    const { rows } = await this.pool.query<SlaConfigVersion>(
      `SELECT * FROM sla_config_versions
       ORDER BY changed_at DESC
       LIMIT $1 OFFSET $2`,
      [limit, offset]
    );
    return rows;
  }
}
