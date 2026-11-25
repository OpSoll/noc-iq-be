import pytest
from app.api.v1.endpoints.sla import calculate_sla_performance

def test_sla_performance_empty_window_semantics():
    response = calculate_sla_performance([])

    assert response.is_empty is True
    assert response.total_incidents == 0
    assert response.average_mttr_minutes == 0.0
    assert response.p50_mttr_minutes is None
    assert response.p95_mttr_minutes is None

def test_sla_performance_populated_window_semantics():
    response = calculate_sla_performance([15.0, 30.0, 45.0])

    assert response.is_empty is False
    assert response.total_incidents == 3
    assert response.average_mttr_minutes == 30.0
    assert response.p50_mttr_minutes is not None
    assert response.p95_mttr_minutes is not None