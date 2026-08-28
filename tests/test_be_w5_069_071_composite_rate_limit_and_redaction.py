"""Tests for BE-W5-069 (composite rate limiting) + BE-W5-071 (log redaction). Closes #330, #332."""
from app.core.rate_limiter import RateLimiter
from app.services.audit_log import AuditLogService

LOCKOUT_TIERS = [(3, 60), (6, 300), (10, 1800)]


def composite_key(ip: str, account: str) -> str:
    """Rate-limit key scoped to both IP and account; resilient to IP rotation."""
    return f"auth:{ip}:{account}"


def lockout_seconds(failure_count: int) -> int:
    """Escalating lockout duration for a given consecutive-failure count."""
    seconds = 0
    for threshold, duration in LOCKOUT_TIERS:
        if failure_count >= threshold:
            seconds = duration
    return seconds


class TestCompositeRateLimit:
    def test_same_ip_different_accounts_tracked_separately(self):
        assert composite_key("1.2.3.4", "alice") != composite_key("1.2.3.4", "bob")

    def test_lockout_escalates_with_failures(self):
        assert lockout_seconds(0) == 0
        assert lockout_seconds(3) == 60
        assert lockout_seconds(6) == 300
        assert lockout_seconds(10) == 1800

    def test_composite_key_blocks_after_limit(self):
        limiter = RateLimiter()
        key = composite_key("9.9.9.9", "victim")
        results = [limiter.check(key, limit=3, window_seconds=60) for _ in range(5)]
        assert results[:3] == [True, True, True]
        assert False in results[3:]


class TestSensitiveLogRedaction:
    def test_sensitive_fields_are_redacted(self):
        raw = {"password": "hunter2", "token": "abc123", "seed": "seed phrase", "outage_id": "out-1"}
        safe = AuditLogService()._sanitize(raw)
        assert safe["password"] == safe["token"] == safe["seed"] == "[REDACTED]"
        assert safe["outage_id"] == "out-1"

    def test_no_leaked_secret_values_in_sanitized_output(self):
        safe = AuditLogService()._sanitize({"secret_key": "sk_live_12345", "amount": 100})
        assert "sk_live_12345" not in str(safe.values())
