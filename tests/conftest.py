import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db import make_engine
from app.models.platform import User
from app.schemas.common import RequestContext, Role
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


@pytest.fixture(scope="session")
def kb_indexed(test_engine: Engine) -> None:
    """Index data/kb into the test database once (as the least-privilege app role)."""
    from scripts.build_index import build

    build(get_settings().test_app_database_url)


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


@pytest.fixture(scope="session")
def app_session_factory(test_engine: Engine) -> sessionmaker:
    """Sessions on the test database as the least-privilege app role, like the running API."""
    engine = make_engine(get_settings().test_app_database_url)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def client(app_session_factory: sessionmaker):
    """API client wired to the test database. No lifespan, so models load lazily only if used."""
    from fastapi.testclient import TestClient

    from app.api.ratelimit import limiter
    from app.db import get_db, get_session_factory
    from app.main import app

    def test_db():
        with app_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[get_session_factory] = lambda: app_session_factory
    limiter.reset()
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def login(client):
    def do_login(username: str, password: str) -> dict[str, str]:
        response = client.post("/auth/login", json={"username": username, "password": password})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return do_login


@pytest.fixture
def ctx_for(db: Session):
    """Build the trusted RequestContext the API would build from a JWT for a seeded user."""

    def build(username: str) -> RequestContext:
        user = db.scalars(select(User).where(User.username == username)).one()
        return RequestContext(
            user_id=user.id,
            username=user.username,
            role=Role(user.role),
            patient_id=user.patient_id,
            trace_id=f"TEST-{username}",
        )

    return build
