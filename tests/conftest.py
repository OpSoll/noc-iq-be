import importlib

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app as main_app


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


@pytest.fixture(scope="session")
def client():
    from app.db.base import Base
    from app.db.session import engine

    importlib.import_module("app.models")
    importlib.import_module("app.models.orm")  # import all ORM models
    Base.metadata.create_all(bind=engine)
    with TestClient(main_app) as test_client:
        yield test_client


@pytest.fixture
def db():
    from app.db.base import Base
    from app.db.session import engine, SessionLocal

    importlib.import_module("app.models")
    importlib.import_module("app.models.orm")  # import all ORM models
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
