// SC-069: Overflow-safety tests for large config values

const MAX_SAFE = Number.MAX_SAFE_INTEGER;

interface SlaConfig {
  threshold_minutes: number;
  payout_multiplier: number;
}

function computeScore(mttr: number, config: SlaConfig): number {
  const { threshold_minutes, payout_multiplier } = config;
  if (!Number.isFinite(threshold_minutes) || !Number.isFinite(payout_multiplier)) {
    throw new RangeError("Config values must be finite numbers");
  }
  if (threshold_minutes <= 0 || payout_multiplier <= 0) {
    throw new RangeError("Config values must be positive");
  }
  if (mttr <= threshold_minutes) return 100;
  const penalty = (mttr - threshold_minutes) * payout_multiplier;
  if (!Number.isFinite(penalty) || penalty > MAX_SAFE) {
    throw new RangeError(`Overflow: penalty=${penalty} exceeds safe range`);
  }
  return Math.max(0, 100 - penalty);
}

function assertThrows(fn: () => unknown, label: string): void {
  try {
    fn();
    throw new Error(`Expected throw for: ${label}`);
  } catch (e) {
    if (e instanceof RangeError) return;
    throw e;
  }
}

// Large but safe values
const safe = computeScore(1000, { threshold_minutes: 500, payout_multiplier: 2 });
if (safe !== 0) throw new Error(`Expected 0, got ${safe}`);

// Overflow: multiplier causes penalty > MAX_SAFE
assertThrows(
  () => computeScore(MAX_SAFE, { threshold_minutes: 1, payout_multiplier: MAX_SAFE }),
  "overflow multiplier"
);

// Non-finite config
assertThrows(() => computeScore(10, { threshold_minutes: Infinity, payout_multiplier: 1 }), "infinite threshold");
assertThrows(() => computeScore(10, { threshold_minutes: 60, payout_multiplier: NaN }), "NaN multiplier");

// Zero/negative config
assertThrows(() => computeScore(10, { threshold_minutes: 0, payout_multiplier: 1 }), "zero threshold");
assertThrows(() => computeScore(10, { threshold_minutes: 60, payout_multiplier: -1 }), "negative multiplier");

export { computeScore };
