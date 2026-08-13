import pytest
from fastapi.testclient import TestClient

import importlib
from app.main import app as main_app
from app.db.session import SessionLocal


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
    from app.db.session import engine
    importlib.import_module("app.models")
    importlib.import_module("app.models.orm")  # import all ORM models
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
