// Shared test factories for outages, SLA results, payments, and auth objects.
// Import from here instead of hand-building objects in individual test files.

export type Outage = {
  id: string;
  service: string;
  started_at: string;
  resolved_at: string | null;
  mttr_minutes: number | null;
};

export type SLAResult = {
  outage_id: string;
  sla_met: boolean;
  score: number;
  computed_at: string;
};

export type Payment = {
  id: string;
  wallet_id: string;
  amount: number;
  currency: string;
  status: "pending" | "confirmed" | "failed";
};

export type AuthUser = {
  id: string;
  email: string;
  role: "admin" | "viewer";
  token: string;
};

let seq = 0;
const next = () => String(++seq);

export function makeOutage(overrides: Partial<Outage> = {}): Outage {
  return {
    id: `outage-${next()}`,
    service: "core-api",
    started_at: "2026-01-01T00:00:00Z",
    resolved_at: "2026-01-01T01:00:00Z",
    mttr_minutes: 60,
    ...overrides,
  };
}

export function makeSLAResult(overrides: Partial<SLAResult> = {}): SLAResult {
  return {
    outage_id: `outage-${next()}`,
    sla_met: true,
    score: 99.5,
    computed_at: "2026-01-01T02:00:00Z",
    ...overrides,
  };
}

export function makePayment(overrides: Partial<Payment> = {}): Payment {
  return {
    id: `pay-${next()}`,
    wallet_id: `wallet-${next()}`,
    amount: 100,
    currency: "USD",
    status: "confirmed",
    ...overrides,
  };
}

export function makeAuthUser(overrides: Partial<AuthUser> = {}): AuthUser {
  return {
    id: `user-${next()}`,
    email: `user${next()}@example.com`,
    role: "viewer",
    token: `tok-${Math.random().toString(36).slice(2)}`,
    ...overrides,
  };
}
