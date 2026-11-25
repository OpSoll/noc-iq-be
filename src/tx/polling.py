import time

TRANSIENT_STATES = {"pending", "unknown", "not_found", "error"}


def poll_tx_status(fetch_status, deadline_seconds=60.0, interval=2.0, clock=time.monotonic, sleep=time.sleep):
    """Poll ``fetch_status()`` until a terminal state or the elapsed deadline.

    Transient states never cause a failure: when the deadline is reached the
    result is ``"pending"`` (unknown), not ``"failed"``.
    """
    start = clock()
    while True:
        status = fetch_status()
        if status not in TRANSIENT_STATES:
            return status
        remaining = deadline_seconds - (clock() - start)
        if remaining <= 0:
            return "pending"
        sleep(min(interval, remaining))
