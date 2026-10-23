// BE-094: Route-level response examples and OpenAPI hardening

export interface OutageExample {
  id: string;
  severity: "low" | "medium" | "high" | "critical";
  mttr_minutes: number;
  resolved: boolean;
}

export interface SlaExample {
  outage_id: string;
  sla_met: boolean;
  score: number;
  threshold_minutes: number;
}

export interface ErrorExample {
  detail: string;
  code: number;
}

export const outageResponseExample: OutageExample = {
  id: "outage-001",
  severity: "high",
  mttr_minutes: 45,
  resolved: true,
};

export const slaResponseExample: SlaExample = {
  outage_id: "outage-001",
  sla_met: true,
  score: 92.5,
  threshold_minutes: 60,
};

export const errorResponseExample: ErrorExample = {
  detail: "Outage not found",
  code: 404,
};

export const openApiExamples = {
  "/api/v1/outages": {
    GET: { "200": outageResponseExample },
    POST: { "201": outageResponseExample, "422": errorResponseExample },
  },
  "/api/v1/sla": {
    GET: { "200": slaResponseExample, "404": errorResponseExample },
  },
} as const;

export function validateExampleShape(example: unknown): boolean {
  return example !== null && typeof example === "object";
}
