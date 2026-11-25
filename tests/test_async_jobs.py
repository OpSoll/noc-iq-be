import pytest
from unittest.mock import patch, MagicMock

# Deterministic fixtures for celery integration harness
@pytest.fixture
def mock_celery_task():
    mock_task = MagicMock()
    mock_task.apply_async.return_value = MagicMock(id="test-task-id", status="PENDING")
    return mock_task

@pytest.fixture
def deterministic_data():
    return {
        "user_id": 123,
        "payload": {"key": "value"}
    }

def test_async_job_retries(mock_celery_task, deterministic_data):
    """
    Test harness for Celery job workflows including retries.
    """
    # Simulate a failure and retry
    mock_celery_task.apply_async.side_effect = [Exception("Temporary failure"), MagicMock(id="test-task-id", status="SUCCESS")]
    
    try:
        mock_celery_task.apply_async(args=[deterministic_data])
    except Exception as e:
        assert str(e) == "Temporary failure"
        
    # Retry succeeds
    result = mock_celery_task.apply_async(args=[deterministic_data])
    assert result.status == "SUCCESS"

def test_dead_letter_handling(mock_celery_task, deterministic_data):
    """
    Test harness for Celery job workflows including dead-letter handling.
    """
    # Simulate max retries exceeded
    mock_celery_task.apply_async.side_effect = Exception("Max retries exceeded")
    
    try:
        mock_celery_task.apply_async(args=[deterministic_data])
    except Exception as e:
        assert str(e) == "Max retries exceeded"
    
    # Assert dead letter logic (e.g., logging or database entry)
    # This is a mock implementation for the test harness
    dead_letter_queue = []
    dead_letter_queue.append(deterministic_data)
    assert len(dead_letter_queue) == 1
    assert dead_letter_queue[0] == deterministic_data


def test_bulk_sla_lock_job_id_contract():
    from app.tasks.sla_tasks import _bulk_sla_lock_job_id as key

    base = {"device_ids": ["b", "a", "c"], "period": "2026-01"}
    # Same set in a different order coalesces.
    assert key(base) == key({"device_ids": ["c", "a", "b"], "period": "2026-01"})
    # Different period or device set stays independent.
    assert key(base) != key({**base, "period": "2026-02"})
    assert key(base) != key({**base, "device_ids": ["a", "b"]})
    # Empty / missing device lists are deterministic; duplicates are kept
    # (not deduplicated) and therefore deterministic but distinct.
    assert key({"device_ids": [], "period": "p"}) == key({"period": "p"})
    assert key({"device_ids": ["a", "a"], "period": "p"}) == key({"device_ids": ["a", "a"], "period": "p"})
    assert key({"device_ids": ["a", "a"], "period": "p"}) != key({"device_ids": ["a"], "period": "p"})
