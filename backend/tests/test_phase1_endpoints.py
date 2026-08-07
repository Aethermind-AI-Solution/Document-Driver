from app import auth
from app.models import User


def _seed(db, email="u@x.co", role="reviewer", pw="password1"):
    db.add(User(email=email, password_hash=auth.hash_password(pw), role=role, is_active=True))
    db.commit()
    return db.query(User).filter_by(email=email).first()


def test_login_success_and_me(client, db_session):
    from app.main import app
    app.dependency_overrides.pop(auth.get_current_user, None)
    _seed(db_session, role="admin")
    r = client.post("/auth/login", json={"email": "u@x.co", "password": "password1"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    assert r.json()["user"]["role"] == "admin"
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["email"] == "u@x.co"


def test_login_bad_password(client, db_session):
    _seed(db_session)
    r = client.post("/auth/login", json={"email": "u@x.co", "password": "nope"})
    assert r.status_code == 401
