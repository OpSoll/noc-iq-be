from app.services.trace_guards import mask, order_timeline, parse_traceparent, task_headers

VALID = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def test_equal_timestamps_stable():
    events = [{"id": 3, "timestamp": 1}, {"id": 1, "timestamp": 1}, {"id": 2, "timestamp": 0}]
    first = order_timeline(events)
    assert [e["id"] for e in first] == [2, 1, 3]
    assert order_timeline(list(reversed(events))) == first


def test_nested_masking():
    data = {"user": "a", "auth": {"password": "x", "keys": [{"private_key": "k", "name": "n"}]}, "token": "t"}
    masked = mask(data)
    assert masked["token"] == "***" and masked["auth"]["password"] == "***"
    assert masked["auth"]["keys"][0] == {"private_key": "***", "name": "n"}
    assert masked["user"] == "a"


def test_celery_headers_propagate_or_generate():
    assert task_headers("abc", "cid") == {"trace_id": "abc", "correlation_id": "cid"}
    fresh = task_headers()
    assert len(fresh["trace_id"]) == 32 and fresh["correlation_id"]


def test_oversized_and_malformed_traceparent():
    assert parse_traceparent(VALID) == VALID
    assert parse_traceparent("x" * 10_000) is None
    assert parse_traceparent("garbage") is None
