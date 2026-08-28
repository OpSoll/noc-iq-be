import logging

logger = logging.getLogger("security")

class SecurityHeadersMiddleware:
    """Injects security-hardening response headers (HSTS, CSP, nosniff)."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"strict-transport-security", b"max-age=63072000; includeSubDomains"))
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"content-security-policy", b"default-src 'self'"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)

class BruteForceProtector:
    """Maintains IP-specific lockout counters for verification endpoints."""
    def __init__(self, max_attempts: int = 5):
        self.max_attempts = max_attempts
        self.failures = {}

    def log_failure(self, ip_address: str):
        self.failures[ip_address] = self.failures.get(ip_address, 0) + 1
        if self.failures[ip_address] >= self.max_attempts:
            logger.warning(f"IP address {ip_address} locked out due to brute-force attempts.")

    def is_locked_out(self, ip_address: str) -> bool:
        return self.failures.get(ip_address, 0) >= self.max_attempts
