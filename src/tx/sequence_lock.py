import threading

_lock = threading.Lock()


def acquire_sequence_lock(sign):
    """Run ``sign`` under the sequence lock; the lock is always released,
    including when signing raises, so the next transaction is not blocked."""
    with _lock:
        return sign()
