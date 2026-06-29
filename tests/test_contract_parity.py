import pytest

def test_contract_parity():
    """
    Automate parity checks that flag contract drift likely to break frontend integrations.
    """
    expected_fe_contract = {
        "id": int,
        "name": str,
        "email": str,
        "is_active": bool
    }
    
    # Mock backend response shape
    backend_response_shape = {
        "id": int,
        "name": str,
        "email": str,
        "is_active": bool
    }
    
    drift_failures = []
    
    for field, expected_type in expected_fe_contract.items():
        if field not in backend_response_shape:
            drift_failures.append(f"Missing field: {field}")
        elif backend_response_shape[field] != expected_type:
            drift_failures.append(f"Type mismatch for {field}: expected {expected_type}, got {backend_response_shape[field]}")
            
    if drift_failures:
        pytest.fail(f"Contract drift detected:\n" + "\n".join(drift_failures))
    else:
        assert True

def test_contract_parity_drift_detection():
    """
    Test that drift failures provide field-level diffs.
    """
    expected_fe_contract = {
        "id": int,
        "name": str,
        "email": str,
        "is_active": bool
    }
    
    # Mock backend response shape with drift
    backend_response_shape = {
        "id": str, # Type mismatch
        "name": str,
        # "email": str, # Missing field
        "is_active": bool,
        "extra_field": str # Unused field (not necessarily drift, but can be checked)
    }
    
    drift_failures = []
    
    for field, expected_type in expected_fe_contract.items():
        if field not in backend_response_shape:
            drift_failures.append(f"Missing field: {field}")
        elif backend_response_shape[field] != expected_type:
            drift_failures.append(f"Type mismatch for {field}: expected {expected_type.__name__}, got {backend_response_shape[field].__name__}")
            
    assert "Missing field: email" in drift_failures
    assert "Type mismatch for id: expected int, got str" in drift_failures
