import logging
from types import SimpleNamespace

from app.services import observability_extras as obs


def test_missing_migration_detection():
    assert obs.missing_migrations({"a", "b", "c"}, {"a"}, exclusions={"c"}) == ["b"]
    assert obs.missing_migrations({"a"}, {"a"}) == []


def test_route_template_label():
    req = SimpleNamespace(scope={"route": SimpleNamespace(path="/outages/{outage_id}")})
    assert obs.route_label(req) == "/outages/{outage_id}"
    assert obs.route_label(SimpleNamespace(scope={})) == "unmatched"


def test_4xx_counter_normalized():
    obs.ERROR_4XX.clear()
    for status in (404, 404, 422, 418, 500, 200):
        obs.record_status(status)
    assert obs.ERROR_4XX == {"not_found": 2, "validation": 1, "other_4xx": 1}


def test_slow_query_logged_without_params(caplog):
    with caplog.at_level(logging.WARNING, logger="noc_iq.slow_query"):
        assert obs.log_slow_query("SELECT * FROM outages WHERE id = :id", 900, threshold_ms=100)
        assert not obs.log_slow_query("SELECT 1", 5, threshold_ms=100)
    assert len(caplog.records) == 1
    assert caplog.records[0].duration_ms == 900
