import pytest
from app.api.v1.endpoints.analytics import calculate_sla_violation_rate

def test_sla_violation_rate_grouped_by_severity():
    mock_incidents = [
        {"id": 1, "severity": "P1", "violation": True},
        {"id": 2, "severity": "P1", "violation": False},
        {"id": 3, "severity": "P2", "violation": True},
        {"id": 4, "severity": "P3", "violation": False},
        {"id": 5, "severity": "P3", "violation": False},
    ]

    response = calculate_sla_violation_rate(mock_incidents, group_by_severity=True)

    assert response.total_incidents == 5
    assert response.total_violations == 2
    assert response.overall_violation_rate == 40.0
    assert response.by_severity is not None

    severity_dict = {item.severity: item for item in response.by_severity}
    
    # Check P1 group
    assert severity_dict["P1"].total_incidents == 2
    assert severity_dict["P1"].violation_count == 1
    assert severity_dict["P1"].violation_rate == 50.0

    # Check P2 group
    assert severity_dict["P2"].total_incidents == 1
    assert severity_dict["P2"].violation_count == 1
    assert severity_dict["P2"].violation_rate == 100.0

    # Ensure empty/unrepresented severity groups (e.g. P4) are omitted
    assert "P4" not in severity_dict

def test_sla_violation_rate_ungrouped():
    mock_incidents = [
        {"id": 1, "severity": "P1", "violation": True},
        {"id": 2, "severity": "P2", "violation": False},
    ]

    response = calculate_sla_violation_rate(mock_incidents, group_by_severity=False)
    assert response.total_incidents == 2
    assert response.total_violations == 1
    assert response.by_severity is None