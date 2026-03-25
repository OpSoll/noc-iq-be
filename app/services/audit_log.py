import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class AuditLogStore:
    def __init__(self, file_path: str | None = None):
        default_path = Path(__file__).resolve().parents[2] / ".runtime" / "audit-log.jsonl"
        configured_path = Path(file_path) if file_path else Path(
            os.getenv("NOCIQ_AUDIT_LOG_PATH", default_path)
        )
        self._file_path = configured_path
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_path.touch(exist_ok=True)

    def log(self, action: str, payload: dict[str, Any]):
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "action": action,
            "payload": payload,
        }
        with self._file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def list(self):
        entries = []
        with self._file_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return list(reversed(entries))


audit_log = AuditLogStore()
