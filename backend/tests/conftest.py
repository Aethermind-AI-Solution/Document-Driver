import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app import auth
from app.database import Base, get_db
from app.main import app
from app.models import User


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield session
    finally:
        app.dependency_overrides.clear()
        session.close()


@pytest.fixture
def client(db_session):
    """Existing endpoint tests run authenticated as an admin unless a test
    overrides get_current_user itself."""
    admin = User(email="admin@test.local", password_hash="x", role="admin", is_active=True)
    db_session.add(admin)
    db_session.commit()
    app.dependency_overrides[auth.get_current_user] = lambda: admin
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(auth.get_current_user, None)


from app.security import rate_limiter


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Isolate the shared sliding-window rate limiter between tests (now that
    /auth/login is rate-limited, cross-test accumulation would cause spurious 429s)."""
    rate_limiter.reset()
    yield
    rate_limiter.reset()
