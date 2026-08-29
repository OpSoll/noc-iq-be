// Unit test for the SLA availability tier mapping (issue #557).
// Mirrors tests/test_sla_tier.py for the TypeScript reference module.
// Run with: npx ts-node tests/check_sla_tier.ts
// Exits non-zero if any assertion fails.

import {
  SLATIER_CONFIG,
  SLATier,
  classifyAvailability,
  getTierConfig,
} from "../src/sla-tier";

let failures = 0;

function assertEqual(actual: unknown, expected: unknown, label: string): void {
  if (actual !== expected) {
    console.error(`FAIL ${label}: expected ${String(expected)}, got ${String(actual)}`);
    failures += 1;
  } else {
    console.log(`ok   ${label}`);
  }
}

function assertThrows(fn: () => unknown, label: string): void {
  try {
    fn();
    console.error(`FAIL ${label}: expected throw, none raised`);
    failures += 1;
  } catch {
    console.log(`ok   ${label}`);
  }
}

// Enum structure
assertEqual(Object.keys(SLATier).length, 3, "SLATier has exactly 3 members");
assertEqual(SLATier.TIER_1, "tier_1", "SLATier.TIER_1 value");
assertEqual(SLATier.TIER_2, "tier_2", "SLATier.TIER_2 value");
assertEqual(SLATier.TIER_3, "tier_3", "SLATier.TIER_3 value");

// Tier mapping: thresholds + penalty rates
assertEqual(Object.keys(SLATIER_CONFIG).length, 3, "every tier has a config");
assertEqual(SLATIER_CONFIG[SLATier.TIER_1].availabilityThreshold, 99.99, "TIER_1 threshold");
assertEqual(SLATIER_CONFIG[SLATier.TIER_2].availabilityThreshold, 99.9, "TIER_2 threshold");
assertEqual(SLATIER_CONFIG[SLATier.TIER_3].availabilityThreshold, 99.0, "TIER_3 threshold");
assertEqual(SLATIER_CONFIG[SLATier.TIER_1].penaltyRate, 100, "TIER_1 penalty rate");
assertEqual(SLATIER_CONFIG[SLATier.TIER_2].penaltyRate, 50, "TIER_2 penalty rate");
assertEqual(SLATIER_CONFIG[SLATier.TIER_3].penaltyRate, 25, "TIER_3 penalty rate");
assertEqual(getTierConfig(SLATier.TIER_2), SLATIER_CONFIG[SLATier.TIER_2], "getTierConfig round-trip");

// Classification boundaries
assertEqual(classifyAvailability(99.99), SLATier.TIER_1, "99.99% -> TIER_1");
assertEqual(classifyAvailability(99.995), SLATier.TIER_1, "99.995% -> TIER_1");
assertEqual(classifyAvailability(100), SLATier.TIER_1, "100% -> TIER_1");
assertEqual(classifyAvailability(99.9), SLATier.TIER_2, "99.9% -> TIER_2");
assertEqual(classifyAvailability(99.91), SLATier.TIER_2, "99.91% -> TIER_2");
assertEqual(classifyAvailability(99.0), SLATier.TIER_3, "99.0% -> TIER_3");
assertEqual(classifyAvailability(99.01), SLATier.TIER_3, "99.01% -> TIER_3");
assertThrows(() => classifyAvailability(98.99), "98.99% below all tiers throws");
assertThrows(() => classifyAvailability(0), "0% below all tiers throws");
assertThrows(() => getTierConfig("tier_4" as SLATier), "unknown tier throws");

if (failures > 0) {
  console.error(`\n${failures} assertion(s) FAILED`);
  process.exit(1);
}
console.log("\nAll SLA tier mapping checks passed.");
