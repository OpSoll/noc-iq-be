"""Readiness must reflect Celery broker availability (#979).

``GET /health/readiness`` probes three dependencies — the database, the Celery
broker, and the worker queue bindings — and combines them:

    overall_ok = db_ok and celery_ok and bool(queue_probe.get("ok"))

The existing coverage (``tests/test_health_endpoints.py:15``) asserts only that
``status`` is *one of* ``{"ok", "degraded"}`` and that each dependency is *one of*
its two possible strings. That passes no matter what the endpoint computes: a
readiness probe that ignored the broker entirely and always returned ``ok`` would
satisfy every existing assertion. An orchestrator cannot tell a healthy instance
from one whose broker is down, so a rolling deploy would send traffic to a worker
that will never pick up a task.

These tests pin the combination logic by controlling each probe independently
rather than accepting whatever the environment happens to report.

Note the patching target: the probes are resolved as module globals inside
``readiness``, so they are patched on ``app.main`` — patching
``app.main.readiness``'s closure or the source module would not take effect.
"""

import pytest
from fastapi.testclient import TestClient

READINESS_URL = "/health/readiness"


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    return TestClient(app)


def set_probes(monkeypatch, *, database: bool, celery: bool, queue: bool) -> None:
    """Force all three probes to a known state.

    ``check_worker_queue_bindings`` returns a dict rather than a bool, so it is
    stubbed with the same shape the endpoint reads (``probe.get("ok")``).
    """
    from app import main

    async def _db():
        return database

    async def _celery():
        return celery

    async def _queue():
        return {
            "ok": queue,
            "required": [],
            "observed": [],
            "missing": [],
            "workers_seen": 1 if queue else 0,
            "timeout_seconds": 5.0,
        }

    monkeypatch.setattr(main, "check_database", _db)
    monkeypatch.setattr(main, "check_celery", _celery)
    monkeypatch.setattr(main, "check_worker_queue_bindings", _queue)


# --------------------------------------------------------------------------- #
# #979 — readiness reflects broker availability
# --------------------------------------------------------------------------- #


class TestReadinessReflectsBroker:
    def test_ready_when_every_dependency_is_up(self, client, monkeypatch):
        set_probes(monkeypatch, database=True, celery=True, queue=True)

        response = client.get(READINESS_URL)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["dependencies"]["celery"] == "ok"

    def test_degraded_when_broker_is_down(self, client, monkeypatch):
        """The case the issue is about: everything else fine, broker unreachable."""
        set_probes(monkeypatch, database=True, celery=False, queue=True)

        response = client.get(READINESS_URL)

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        assert body["dependencies"]["celery"] == "down"

    def test_broker_down_still_reports_other_dependencies_accurately(
        self, client, monkeypatch
    ):
        """A single failing probe must not flatten the rest to "down".

        An operator reading this needs to know the broker is the problem, not that
        everything is.
        """
        set_probes(monkeypatch, database=True, celery=False, queue=True)

        body = client.get(READINESS_URL).json()

        assert body["dependencies"]["database"] == "ok"
        assert body["dependencies"]["celery"] == "down"
        assert body["dependencies"]["queue_bindings"] == "ok"

    def test_degraded_when_database_is_down(self, client, monkeypatch):
        set_probes(monkeypatch, database=False, celery=True, queue=True)

        response = client.get(READINESS_URL)

        assert response.status_code == 503
        assert response.json()["dependencies"]["database"] == "down"

    def test_degraded_when_queue_bindings_are_missing(self, client, monkeypatch):
        """A reachable broker with no bound workers is still not ready.

        This is the failure that a broker-only ping misses: the broker answers
        while every task silently queues forever.
        """
        set_probes(monkeypatch, database=True, celery=True, queue=False)

        response = client.get(READINESS_URL)

        assert response.status_code == 503
        assert response.json()["dependencies"]["queue_bindings"] == "missing"

    def test_all_dependencies_down(self, client, monkeypatch):
        set_probes(monkeypatch, database=False, celery=False, queue=False)

        body = client.get(READINESS_URL).json()

        assert body["status"] == "degraded"
        assert body["dependencies"] == {
            "database": "down",
            "celery": "down",
            "queue_bindings": "missing",
        }


class TestReadinessFailsClosed:
    def test_one_failing_probe_is_enough_to_degrade(self, client, monkeypatch):
        """Each probe alone must be able to fail the gate.

        Parametrised by dependency rather than written three times: the combination is
        an ``and``, and the bug this guards against is one branch of it being
        dropped, which a single case would miss.
        """
        for database, celery, queue, failing in (
            (False, True, True, "database"),
            (True, False, True, "celery"),
            (True, True, False, "queue_bindings"),
        ):
            set_probes(monkeypatch, database=database, celery=celery, queue=queue)

            response = client.get(READINESS_URL)

            assert response.status_code == 503, failing
            assert response.json()["status"] == "degraded", failing

    def test_readiness_does_not_require_authentication(self, client, monkeypatch):
        """An orchestrator probe carries no credentials.

        If this ever gained auth, every orchestrator would see a 401 and treat the
        instance as permanently unready — a fail-closed change that is really a
        total outage of the probe.
        """
        set_probes(monkeypatch, database=True, celery=True, queue=True)

        assert client.get(READINESS_URL).status_code == 200

    def test_readiness_response_always_names_its_dependencies(self, client, monkeypatch):
        """Whatever the state, the shape is stable so parsing never fails."""
        set_probes(monkeypatch, database=False, celery=False, queue=False)

        body = client.get(READINESS_URL).json()

        assert set(body["dependencies"]) == {
            "database",
            "celery",
            "queue_bindings",
        }
        assert "timestamp" in body
        assert "queue_bindings" in body

    def test_liveness_is_unaffected_by_a_down_broker(self, client, monkeypatch):
        """Liveness must not depend on the broker.

        If liveness followed the broker, a broker outage would restart every
        instance in a loop while none of them were the thing that was broken.
        """
        set_probes(monkeypatch, database=True, celery=False, queue=False)

        response = client.get("/health/liveness")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
