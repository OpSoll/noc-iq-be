import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def test_settings():
    settings.RATE_LIMIT_BACKEND = "redis"
    settings.REDIS_URL = "redis://localhost:6379/0"

@pytest.fixture(autouse=True)
def check_db_connection_leak(request):
    # Placeholder mock context for tracking connection leaks in SQLAlchemy sessions
    yield
    # Assertion logic asserting active connection count returns to baseline
    pass
