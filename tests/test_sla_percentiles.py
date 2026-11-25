import pytest
from app.api.v1.endpoints.sla import calculate_sla_performance

def test_calculate_sla_performance_with_data():
    # Resolution times in minutes
    resolution_times = [10.0, 20.0, 30.0, 40.0, 100.0]
    result = calculate_sla_performance(resolution_times)

    assert result.total_incidents == 5
    assert result.average_mttr_minutes == 40.0
    assert result.p50_mttr_minutes == 30.0
    assert result.p95_mttr_minutes == 92.0

def test_calculate_sla_performance_empty_data_null_safety():
    result = calculate_sla_performance([])

    assert result.total_incidents == 0
    assert result.average_mttr_minutes == 0.0
    assert result.p50_mttr_minutes is None
    assert result.p95_mttr_minutes is None