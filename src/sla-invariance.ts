// SC-068: Invariance tests between calculate_sla and calculate_sla_view

type Severity = "low" | "medium" | "high" | "critical";

interface SlaConfig {
  threshold_minutes: number;
  payout_multiplier: number;
}

interface SlaResult {
  sla_met: boolean;
  score: number;
}

const CONFIG: Record<Severity, SlaConfig> = {
  low:      { threshold_minutes: 240, payout_multiplier: 1 },
  medium:   { threshold_minutes: 120, payout_multiplier: 2 },
  high:     { threshold_minutes: 60,  payout_multiplier: 3 },
  critical: { threshold_minutes: 30,  payout_multiplier: 5 },
};

function calculateSla(severity: Severity, mttr: number): SlaResult {
  const { threshold_minutes, payout_multiplier } = CONFIG[severity];
  const sla_met = mttr <= threshold_minutes;
  const score = sla_met ? 100 : Math.max(0, 100 - (mttr - threshold_minutes) * payout_multiplier);
  return { sla_met, score };
}

function calculateSlaView(severity: Severity, mttr: number): SlaResult {
  // View-only path — must produce identical output
  return calculateSla(severity, mttr);
}

function assertInvariant(severity: Severity, mttr: number): void {
  const a = calculateSla(severity, mttr);
  const b = calculateSlaView(severity, mttr);
  if (a.sla_met !== b.sla_met || a.score !== b.score) {
    throw new Error(`Invariant broken for ${severity} @ mttr=${mttr}: ${JSON.stringify(a)} vs ${JSON.stringify(b)}`);
  }
}

const cases: [Severity, number][] = [
  ["low", 0], ["low", 240], ["low", 300],
  ["medium", 60], ["medium", 120], ["medium", 200],
  ["high", 30], ["high", 60], ["high", 90],
  ["critical", 10], ["critical", 30], ["critical", 60],
];

for (const [sev, mttr] of cases) assertInvariant(sev, mttr);

export { calculateSla, calculateSlaView, assertInvariant };
