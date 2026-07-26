import { Router, Request, Response, NextFunction } from 'express';
import { SlaTraceService } from './sla-trace.service';

export function createSlaTraceRouter(service: SlaTraceService): Router {
  const router = Router();

  /**
   * GET /sla-traces
   * Retrieve all SLA calculation traces (paginated)
   */
  router.get('/', async (req: Request, res: Response, next: NextFunction) => {
    try {
      const limit = parseInt((req.query.limit as string) ?? '50', 10);
      const offset = parseInt((req.query.offset as string) ?? '0', 10);

      const traces = await service.getAllTraces(limit, offset);
      res.json({ data: traces, limit, offset });
    } catch (err) {
      next(err);
    }
  });

  /**
   * GET /sla-traces/:id
   * Retrieve a single SLA calculation trace by trace ID
   */
  router.get('/:id', async (req: Request, res: Response, next: NextFunction) => {
    try {
      const trace = await service.getTraceById(req.params.id);
      if (!trace) {
        res.status(404).json({ message: 'Trace not found' });
        return;
      }
      res.json({ data: trace });
    } catch (err) {
      next(err);
    }
  });

  /**
   * GET /sla-traces/incident/:incidentId
   * Retrieve all SLA calculation traces for a specific incident
   */
  router.get(
    '/incident/:incidentId',
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const traces = await service.getTracesByIncident(req.params.incidentId);
        res.json({ data: traces });
      } catch (err) {
        next(err);
      }
    }
  );

  /**
   * POST /sla-traces/calculate
   * Run an SLA calculation and persist the trace
   */
  router.post('/calculate', async (req: Request, res: Response, next: NextFunction) => {
    try {
      const { incident_id, severity, threshold_minutes, opened_at, resolved_at } = req.body;

      if (!incident_id || !severity || threshold_minutes == null || !opened_at) {
        res.status(400).json({
          message: 'incident_id, severity, threshold_minutes, and opened_at are required',
        });
        return;
      }

      const result = await service.calculateAndTrace({
        incident_id,
        severity,
        threshold_minutes: Number(threshold_minutes),
        opened_at: new Date(opened_at),
        resolved_at: resolved_at ? new Date(resolved_at) : null,
      });

      res.status(201).json({ data: result });
    } catch (err) {
      next(err);
    }
  });

  return router;
}
