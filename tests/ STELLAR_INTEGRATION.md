# Stellar Integration Guide

## Network identity and testnet/mainnet separation (#286)

This service is configured for a **single** Stellar network per deployment. Cross-network operations are rejected at the adapter layer and audit-logged.

### Configuration

Set `STELLAR_NETWORK` in your environment:

```
STELLAR_NETWORK=testnet   # development / staging
STELLAR_NETWORK=mainnet   # production
```

The application derives the Horizon URL and network passphrase automatically. You may override the Horizon URL via `STELLAR_HORIZON_URL` if using a private instance.

| Variable | Required | Default | Description |
|---|---|---|---|
| `STELLAR_NETWORK` | No | `testnet` | `testnet` or `mainnet` |
| `STELLAR_HORIZON_URL` | No | canonical URL | Override Horizon base URL |
| `WALLET_HEALTH_RATE_LIMIT_SECONDS` | No | `60` | Minimum seconds between health checks per wallet |

### Network mismatch behaviour

When an operation carries a `wallet_network` that differs from `STELLAR_NETWORK`, the service:

1. Logs a `WARNING` with `audit=True` metadata.
2. Returns `HTTP 409 Conflict` with `code: NETWORK_MISMATCH`.
3. Does **not** submit any transaction.

This prevents accidental mainnet payments from a testnet deployment and vice-versa.

---

## Trustline verification (#285)

Before any payout is submitted, the service verifies that the recipient wallet has an active trustline for the payment asset with a non-zero limit.

| Trustline state | Reason code | HTTP status | Retryable? |
|---|---|---|---|
| Missing | `TRUSTLINE_MISSING` | 402 | No — recipient must add trustline |
| Limit zero | `TRUSTLINE_LIMIT_ZERO` | 402 | No — recipient must raise limit |
| Horizon unreachable | `TRUSTLINE_CHECK_FAILED` | 402 | Yes — transient |

---

## Wallet health endpoint (#288)

```
GET /api/v1/wallets/{address}/health
```

Returns operational readiness for a wallet:

```json
{
  "address": "GAAZI4...",
  "overall": "ready",
  "checked_at": "2025-01-01T00:00:00Z",
  "funding": { "state": "ready", "checked_at": "...", "details": { "xlm_balance": "100.0" } },
  "trustline": { "state": "ready", "checked_at": "...", "details": { "asset_code": "USDC", ... } },
  "network_reachable": { "state": "ready", "checked_at": "...", "details": { "horizon_url": "..." } },
  "rate_limited": false
}
```

Checks are rate-limited (default 60 s). Pass `?force=true` to bypass.

The `/readyz` readiness probe aggregates all tracked wallet health records and returns `HTTP 503` if any wallet is `not_ready`.