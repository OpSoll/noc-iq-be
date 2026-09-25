import { Router, Request, Response, NextFunction } from 'express';
import { SlaConfigHistoryService } from './sla-config-history.service';

export function createSlaConfigHistoryRouter(service: SlaConfigHistoryService): Router {
  const router = Router();

  /**
   * GET /sla-config-history
   * Retrieve all config version entries (paginated)
   */
  router.get('/', async (req: Request, res: Response, next: NextFunction) => {
    try {
      const limit = parseInt((req.query.limit as string) ?? '50', 10);
      const offset = parseInt((req.query.offset as string) ?? '0', 10);

      const history = await service.getAllHistory(limit, offset);
      res.json({ data: history, limit, offset });
    } catch (err) {
      next(err);
    }
  });

  /**
   * GET /sla-config-history/:configId
   * Retrieve full version history for a specific SLA config
   */
  router.get('/:configId', async (req: Request, res: Response, next: NextFunction) => {
    try {
      const history = await service.getHistory(req.params.configId);
      res.json({ data: history });
    } catch (err) {
      next(err);
    }
  });

  /**
   * GET /sla-config-history/:configId/latest
   * Retrieve the most recent version of a config
   */
  router.get('/:configId/latest', async (req: Request, res: Response, next: NextFunction) => {
    try {
      const entry = await service.getLatest(req.params.configId);
      if (!entry) {
        res.status(404).json({ message: 'No history found for this config' });
        return;
      }
      res.json({ data: entry });
    } catch (err) {
      next(err);
    }
  });

  /**
   * GET /sla-config-history/:configId/version/:version
   * Retrieve a specific version of a config
   */
  router.get(
    '/:configId/version/:version',
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const version = parseInt(req.params.version, 10);
        const entry = await service.getVersion(req.params.configId, version);
        if (!entry) {
          res.status(404).json({ message: 'Version not found' });
          return;
        }
        res.json({ data: entry });
      } catch (err) {
        next(err);
      }
    }
  );

  /**
   * GET /sla-config-history/user/:changedBy
   * Retrieve all config changes made by a specific user
   */
  router.get(
    '/user/:changedBy',
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const history = await service.getChangesByUser(req.params.changedBy);
        res.json({ data: history });
      } catch (err) {
        next(err);
      }
    }
  );

  /**
   * POST /sla-config-history
   * Record a new SLA config change and compute the diff against the previous version
   */
  router.post('/', async (req: Request, res: Response, next: NextFunction) => {
    try {
      const { config_id, changed_by, new_config, change_reason } = req.body;

      if (!config_id || !changed_by || !new_config || typeof new_config !== 'object') {
        res.status(400).json({
          message: 'config_id, changed_by, and new_config (object) are required',
        });
        return;
      }

      const version = await service.recordChange({
        config_id,
        changed_by,
        new_config,
        change_reason,
      });

      res.status(201).json({ data: version });
    } catch (err) {
      next(err);
    }
  });

  return router;
}
