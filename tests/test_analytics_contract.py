import pytest
import json
from app.utils.analytics_exporter import AnalyticsExporter

def test_export_contract_schema_integrity():
    exporter = AnalyticsExporter()
    mock_rows = [{"status_scope": "SUCCESS", "transaction_count": 100, "aggregate_volume": 2500.50}]
    
    raw_output = exporter.generate_stabilized_export(mock_rows)
    output = json.loads(raw_output)
    
    # 1. Assert metadata envelope boundaries are firmly locked
    assert "metadata" in output
    assert "data" in output
    assert output["metadata"]["schema_version"] == "1.2.0"
    
    # 2. Prevent critical down-stream field deletions (Contract Preservation)
    required_fields = ["status_scope", "transaction_count", "aggregate_volume"]
    for field in required_fields:
        assert field in output["metadata"]["fields"], f"CRITICAL BREAK: Field '{field}' was removed from the contract dictionary."