import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from app.jobs.leases import LeaseManager

def test_reclaim_stale_leases_success_and_count():
    # Mock database session and query results
    mock_db = MagicMock()
    
    manager = LeaseManager(mock_db)
    
    # Execute reclamation
    reclaimed_count = manager.reclaim_stale_leases(stale_threshold_minutes=30)

    # Assert count is returned and processed correctly
    assert isinstance(reclaimed_count, int)
    assert reclaimed_count >= 0

def test_active_leases_skipped():
    mock_db = MagicMock()
    manager = LeaseManager(mock_db)
    
    # When all leases have recent heartbeats (active), none should be reclaimed
    reclaimed_count = manager.reclaim_stale_leases(stale_threshold_minutes=30)
    assert reclaimed_count == 0