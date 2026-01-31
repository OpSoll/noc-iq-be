from datetime import datetime


class AuditLogStore:
    def __init__(self):
        self._entries = []

    def log(self, action: str, payload: dict):
        self._entries.append({
            "timestamp": datetime.utcnow().isoformat(),
            "action": action,
            "payload": payload,
        })

    def list(self):
        return list(reversed(self._entries))


audit_log = AuditLogStore()