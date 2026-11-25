"""Guards for contract mode (#1031), canonical numbers (#1032), circuit (#1033), idempotency (#1034)."""
import math
import threading
import time

SUPPORTED_MODES = {"local", "contract"}


class ContractConfigError(ValueError):
    pass


def select_execution_mode(mode, bridge_url=None):
    value = (mode or "local").strip().lower()
    if value not in SUPPORTED_MODES:
        raise ContractConfigError(f"unsupported CONTRACT_EXECUTION_MODE: {mode!r}")
    if value == "contract" and not bridge_url:
        raise ContractConfigError("contract mode requires a bridge URL")
    return value


def reject_non_finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite numeric value in canonical payload")
    for v in value.values() if isinstance(value, dict) else value if isinstance(value, (list, tuple)) else ():
        reject_non_finite(v)
    return value


class CircuitGuard:
    def __init__(self, threshold=3, cooldown=30.0, probes=1, clock=time.monotonic):
        self.threshold, self.cooldown, self.probes, self.clock = threshold, cooldown, probes, clock
        self.failures, self.opened_at, self.in_flight = 0, None, 0
        self._lock = threading.Lock()

    def record_failure(self):
        with self._lock:
            self.failures += 1
            if self.failures >= self.threshold and self.opened_at is None:
                self.opened_at = self.clock()

    def allow(self):
        with self._lock:
            if self.opened_at is None:
                return True
            if self.clock() - self.opened_at < self.cooldown or self.in_flight >= self.probes:
                return False
            self.in_flight += 1
            return True


def key_reusable(created_at, ttl, now):
    return now > created_at + ttl


def check_key_conflict(stored_digest, new_digest, created_at, ttl, now):
    if not key_reusable(created_at, ttl, now) and stored_digest != new_digest:
        raise ValueError("idempotency key conflict")
    return True
