import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import make_engine
from scripts.reset_db import reset
from scripts.seed_db import seed


@pytest.fixture(scope="session")
def test_engine() -> Engine:
    """Fresh schema + seed data in the test database, once per test session."""
    settings = get_settings()
    reset(settings.test_database_url, settings.db_app_user)
    seed(settings.test_database_url)
    engine = make_engine(settings.test_database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def db(test_engine: Engine) -> Session:
    """Session whose changes are rolled back after each test."""
    connection = test_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    transaction.rollback()
    connection.close()
