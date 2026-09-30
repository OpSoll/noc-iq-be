// SLA availability tier classification (issue #557).
// TypeScript reference mirroring app/services/sla/tiers.py.
//
// Structured SLA tier classification enum with per-tier availability
// thresholds and penalty rates.

export enum SLATier {
  /** 99.99% availability */
  TIER_1 = "tier_1",
  /** 99.9% availability */
  TIER_2 = "tier_2",
  /** 99.0% availability */
  TIER_3 = "tier_3",
}

export interface SLATierConfig {
  tier: SLATier;
  /** Guaranteed availability target (percent uptime) for this tier. */
  availabilityThreshold: number;
  /**
   * Penalty credits per percentage point of availability shortfall below the
   * tier threshold. The 100/50/25 ladder mirrors the repo's severity penalty
   * conventions (critical/high/medium) in app/services/sla/config.py.
   */
  penaltyRate: number;
}

export const SLATIER_CONFIG: Record<SLATier, SLATierConfig> = {
  [SLATier.TIER_1]: { tier: SLATier.TIER_1, availabilityThreshold: 99.99, penaltyRate: 100 },
  [SLATier.TIER_2]: { tier: SLATier.TIER_2, availabilityThreshold: 99.9, penaltyRate: 50 },
  [SLATier.TIER_3]: { tier: SLATier.TIER_3, availabilityThreshold: 99.0, penaltyRate: 25 },
};

export function getTierConfig(tier: SLATier): SLATierConfig {
  const config = SLATIER_CONFIG[tier];
  if (!config) {
    throw new Error(`Unknown SLA tier: ${String(tier)}`);
  }
  return config;
}

/**
 * Classify an observed availability percentage into the strictest SLA tier.
 * Throws when availability is below the minimum TIER_3 threshold.
 */
export function classifyAvailability(availabilityPct: number): SLATier {
  for (const tier of [SLATier.TIER_1, SLATier.TIER_2, SLATier.TIER_3]) {
    const threshold = SLATIER_CONFIG[tier].availabilityThreshold;
    if (availabilityPct >= threshold) {
      return tier;
    }
  }
  throw new Error(
    `Availability ${availabilityPct}% is below the minimum SLA tier threshold ` +
      `(${SLATIER_CONFIG[SLATier.TIER_3].availabilityThreshold}%)`
  );
}
