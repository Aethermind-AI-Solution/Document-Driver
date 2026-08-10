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


def test_admin_can_create_and_list_users(client, db_session):
    # autouse override authenticates as admin
    r = client.post("/users", json={"email": "new@x.co", "password": "password1", "role": "viewer"})
    assert r.status_code == 201 and r.json()["role"] == "viewer"
    dup = client.post("/users", json={"email": "new@x.co", "password": "password1", "role": "viewer"})
    assert dup.status_code == 409
    listed = client.get("/users")
    assert any(u["email"] == "new@x.co" for u in listed.json())


def test_non_admin_cannot_manage_users(client, db_session):
    from app.main import app
    reviewer = _seed(db_session, email="rev@x.co", role="reviewer")
    app.dependency_overrides[auth.get_current_user] = lambda: reviewer
    try:
        assert client.post("/users", json={"email": "z@x.co", "password": "password1"}).status_code == 403
    finally:
        app.dependency_overrides.pop(auth.get_current_user, None)


def test_bootstrap_admin_is_idempotent(db_session, monkeypatch):
    from app import config, services
    monkeypatch.setattr(config, "ADMIN_EMAIL", "boss@x.co")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "password1")
    services.bootstrap_admin(db_session)
    services.bootstrap_admin(db_session)  # second call must not duplicate
    from app.models import User
    assert db_session.query(User).filter_by(email="boss@x.co").count() == 1


def test_patch_user_invalid_role_rejected(client, db_session):
    user = _seed(db_session, email="patchme@x.co", role="reviewer")
    r = client.patch(f"/users/{user.id}", params={"role": "superadmin"})
    assert r.status_code == 400


def test_login_is_rate_limited(client, db_session):
    from app import config
    last = None
    for _ in range(config.RATE_LIMIT_MAX + 1):
        last = client.post("/auth/login", json={"email": "x@x.co", "password": "x"})
    assert last.status_code == 429
