import pytest
from app import auth
from app.models import User


def test_hash_and_verify_password():
    h = auth.hash_password("s3cret!")
    assert h != "s3cret!"
    assert auth.verify_password("s3cret!", h) is True
    assert auth.verify_password("wrong", h) is False


def test_token_round_trip():
    user = User(id=7, email="a@b.co", password_hash="x", role="admin", is_active=True)
    token = auth.create_access_token(user)
    claims = auth.decode_token(token)
    assert claims["sub"] == "7" and claims["email"] == "a@b.co" and claims["role"] == "admin"


def test_expired_token_rejected(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "JWT_EXPIRE_HOURS", -1)  # already expired
    user = User(id=1, email="a@b.co", password_hash="x", role="viewer", is_active=True)
    token = auth.create_access_token(user)
    with pytest.raises(auth.AuthError):
        auth.decode_token(token)
