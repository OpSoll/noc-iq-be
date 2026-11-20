"""Unit tests for the readiness service aggregation (#900)."""

from app.services.readiness import ReadinessService


class FakeSession:
    """Minimal session double: ``execute`` either succeeds or raises."""

    def __init__(self, error=None):
        self.error = error

    def execute(self, statement):
        if self.error is not None:
            raise self.error
        return None


def test_healthy_dependencies_report_ok():
    result = ReadinessService().check_dependencies(FakeSession())

    assert result.status == "ok"
    assert result.dependencies["database"].status == "up"
    assert result.dependencies["redis"].status == "up"


def test_database_failure_degrades_the_service():
    result = ReadinessService().check_dependencies(FakeSession(RuntimeError("db down")))

    assert result.status == "degraded"
    assert result.dependencies["database"].status == "down"


def test_dependency_down_includes_the_error_detail():
    result = ReadinessService().check_dependencies(FakeSession(RuntimeError("boom")))

    database = result.dependencies["database"]
    assert database.latency_ms == 0
    assert database.details["error"] == "boom"


def test_database_failure_does_not_hide_other_dependencies():
    result = ReadinessService().check_dependencies(FakeSession(RuntimeError("db down")))

    assert result.dependencies["redis"].status == "up"
