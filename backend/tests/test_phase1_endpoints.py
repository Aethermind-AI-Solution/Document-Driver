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


def test_login_inactive_user(client, db_session):
    from app.main import app
    app.dependency_overrides.pop(auth.get_current_user, None)
    db_session.add(User(email="inactive@x.co", password_hash=auth.hash_password("password1"), role="reviewer", is_active=False))
    db_session.commit()
    r = client.post("/auth/login", json={"email": "inactive@x.co", "password": "password1"})
    assert r.status_code == 401


def test_change_password_wrong_current(client, db_session):
    from app.main import app
    app.dependency_overrides.pop(auth.get_current_user, None)
    user = _seed(db_session, email="change@x.co", role="reviewer", pw="password1")
    token = auth.create_access_token(user)
    r = client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "wrongpassword", "new_password": "newpassword2"}
    )
    assert r.status_code == 400


def test_change_password_success(client, db_session):
    from app.main import app
    app.dependency_overrides.pop(auth.get_current_user, None)
    user = _seed(db_session, email="changeok@x.co", role="reviewer", pw="password1")
    token = auth.create_access_token(user)

    # Change password with correct current password
    r = client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "password1", "new_password": "newpassword2"}
    )
    assert r.status_code == 200

    # Verify new password works
    login_new = client.post("/auth/login", json={"email": "changeok@x.co", "password": "newpassword2"})
    assert login_new.status_code == 200

    # Verify old password doesn't work
    login_old = client.post("/auth/login", json={"email": "changeok@x.co", "password": "password1"})
    assert login_old.status_code == 401
