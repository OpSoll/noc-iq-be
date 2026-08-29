import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def test_settings():
    settings.RATE_LIMIT_BACKEND = "redis"
    settings.REDIS_URL = "redis://localhost:6379/0"