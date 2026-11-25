import pytest
from datetime import datetime
from app.api.v1.endpoints.analytics import get_bucket_key

def test_daily_bucket_boundaries():
    # Test normal day vs month/year transition
    dt1 = datetime(2026, 10, 31, 23, 59, 59)
    dt2 = datetime(2026, 11, 1, 0, 0, 0)

    assert get_bucket_key(dt1, 'daily') == '2026-10-31'
    assert get_bucket_key(dt2, 'daily') == '2026-11-01'

def test_weekly_bucket_boundaries():
    # Test week rollover (ISO week starts on Monday)
    # Sunday Oct 25, 2026 vs Monday Oct 26, 2026
    sunday = datetime(2026, 10, 25, 12, 0, 0)
    monday = datetime(2026, 10, 26, 12, 0, 0)

    # Sunday belongs to the previous week starting Oct 19
    assert get_bucket_key(sunday, 'weekly') == '2026-10-19'
    # Monday belongs to the new week starting Oct 26
    assert get_bucket_key(monday, 'weekly') == '2026-10-26'

def test_monthly_bucket_boundaries():
    # Test month and year rollover (e.g., Dec 31 to Jan 1)
    dec_end = datetime(2026, 12, 31, 23, 59, 59)
    jan_start = datetime(2027, 1, 1, 0, 0, 0)

    assert get_bucket_key(dec_end, 'monthly') == '2026-12'
    assert get_bucket_key(jan_start, 'monthly') == '2027-01'

def test_leap_year_february_boundary():
    # Leap year February 28 vs 29
    feb_28 = datetime(2024, 2, 28, 23, 59, 59)
    feb_29 = datetime(2024, 2, 29, 12, 0, 0)
    mar_01 = datetime(2024, 3, 1, 0, 0, 0)

    assert get_bucket_key(feb_28, 'daily') == '2024-02-28'
    assert get_bucket_key(feb_29, 'daily') == '2024-02-29'
    assert get_bucket_key(mar_01, 'monthly') == '2024-03'