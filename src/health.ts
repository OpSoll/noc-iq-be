// BE-093: Split health, readiness, and dependency checks

interface HealthStatus {
  status: "ok" | "degraded" | "down";
  timestamp: string;
}

interface ReadinessStatus extends HealthStatus {
  dependencies: Record<string, "ok" | "down">;
}

async function checkDatabase(): Promise<"ok" | "down"> {
  try {
    // Simulate a lightweight DB ping (e.g. SELECT 1)
    const reachable = await Promise.resolve(true);
    return reachable ? "ok" : "down";
  } catch {
    return "down";
  }
}

async function checkWorker(): Promise<"ok" | "down"> {
  try {
    // Simulate a Celery/Redis worker heartbeat check
    const alive = await Promise.resolve(true);
    return alive ? "ok" : "down";
  } catch {
    return "down";
  }
}

export function liveness(): HealthStatus {
  return { status: "ok", timestamp: new Date().toISOString() };
}

export async function readiness(): Promise<ReadinessStatus> {
  const [db, worker] = await Promise.all([checkDatabase(), checkWorker()]);

  const allOk = db === "ok" && worker === "ok";

  return {
    status: allOk ? "ok" : "degraded",
    timestamp: new Date().toISOString(),
    dependencies: { database: db, worker },
  };
}

export async function dependencyReport(): Promise<Record<string, "ok" | "down">> {
  const [db, worker] = await Promise.all([checkDatabase(), checkWorker()]);
  return { database: db, worker };
}
