from fastapi import Request, HTTPException, status

class ContentLengthLimitMiddleware:
    """Enforces Content-Length header checks to reject payloads exceeding 10MB."""
    def __init__(self, app, max_bytes: int = 10 * 1024 * 1024):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            content_length = int(headers.get(b"content-length", 0))
            if content_length > self.max_bytes:
                # payload too large
                await send({
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [(b"content-type", b"text/plain")]
                })
                await send({
                    "type": "http.response.body",
                    "body": b"Payload Too Large"
                })
                return
        await self.app(scope, receive, send)

ROLE_PERMISSIONS = {
    "admin": ["read", "write", "delete"],
    "operator": ["read", "write"],
    "viewer": ["read"]
}

def verify_rbac_permission(user_role: str, required_permission: str):
    """Asserts if a user's role grants required API action capability."""
    allowed = ROLE_PERMISSIONS.get(user_role, [])
    if required_permission not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation forbidden for current user role."
        )
