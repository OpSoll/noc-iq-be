"""Small outage/payment guards: optimistic versions, UTC duration, tx-hash dedupe, network check."""
import threading
from datetime import timezone


class VersionConflict(Exception):
    """Stale optimistic update (#1058)."""


class DuplicateTransaction(Exception):
    """Transaction hash already settled (#1065)."""


def apply_update(record, expected_version, changes):
    if record["version"] != expected_version:
        raise VersionConflict(f"expected v{expected_version}, found v{record['version']}")
    record.update(changes)
    record["version"] += 1
    return record


def duration_minutes(start, end):
    """Elapsed minutes on UTC instants, so DST shifts don't skew it (#1059)."""
    delta = end.astimezone(timezone.utc) - start.astimezone(timezone.utc)
    return int(delta.total_seconds() // 60)


class TxRegistry:
    def __init__(self):
        self._seen, self._lock = {}, threading.Lock()

    def insert(self, tx_hash, row):
        with self._lock:
            if tx_hash in self._seen:
                raise DuplicateTransaction(tx_hash)
            self._seen[tx_hash] = row
            return row


def check_callback_network(payment_network, callback_network, audit):
    """Reject unknown network before any mutation; audit a sanitized reason (#1066)."""
    if callback_network != payment_network:
        audit.append({"event": "callback_rejected", "reason": "network_mismatch"})
        return False
    return True
