import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app import auth
from app.database import Base, get_db
from app.main import app
from app.models import Organization, User


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    session.add(Organization(id=1, name="Default Organization"))
    session.commit()

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
    from app.context import set_current_org
    admin = User(email="admin@test.local", password_hash="x", role="admin", is_active=True, org_id=1)
    db_session.add(admin)
    db_session.commit()

    def _override_current_user():
        # set org context per-request (the request runs in its own context), so
        # the fail-closed loader-criteria has an org during endpoint DB queries.
        set_current_org(admin.org_id)
        return admin

    app.dependency_overrides[auth.get_current_user] = _override_current_user
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


@pytest.fixture(autouse=True)
def _default_org_context():
    """Set a default org context (org 1) for every test, so direct-call unit
    tests (pipeline/jobs/services/mcp) have an org once the fail-closed
    loader-criteria goes live. A test that needs to exercise the unset/raise
    path resets it explicitly (context.set_current_org(None))."""
    from app import context
    token = context.set_current_org(1)
    yield
    context.reset_org(token)
