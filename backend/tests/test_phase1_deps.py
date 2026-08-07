import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from app import auth
from app.database import get_db
from app.models import User


def _app_with_route(dep):
    app = FastAPI()

    @app.get("/probe")
    def probe(user: User = Depends(dep)):
        return {"email": user.email, "role": user.role}
    return app


def test_get_current_user_rejects_missing_token(db_session):
    app = _app_with_route(auth.get_current_user)
    app.dependency_overrides[get_db] = lambda: (yield db_session)
    assert TestClient(app).get("/probe").status_code == 401


def test_require_role_allows_and_denies(db_session):
    db_session.add(User(email="r@x.co", password_hash=auth.hash_password("p"),
                        role="reviewer", is_active=True))
    db_session.commit()
    user = db_session.query(User).filter_by(email="r@x.co").first()
    token = auth.create_access_token(user)
    app = _app_with_route(auth.require_role("reviewer", "admin"))
    app.dependency_overrides[get_db] = lambda: (yield db_session)
    client = TestClient(app)
    ok = client.get("/probe", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200 and ok.json()["role"] == "reviewer"

    app2 = _app_with_route(auth.require_role("admin"))
    app2.dependency_overrides[get_db] = lambda: (yield db_session)
    denied = TestClient(app2).get("/probe", headers={"Authorization": f"Bearer {token}"})
    assert denied.status_code == 403
