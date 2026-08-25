from datetime import datetime, timedelta, timezone
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session
from . import config
from .database import get_db
from .models import User

_ph = PasswordHasher()


class AuthError(Exception):
    pass


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user.id), "email": user.email, "role": user.role,
               "org_id": user.org_id,
               "iat": now, "exp": now + timedelta(hours=config.JWT_EXPIRE_HOURS)}
    return jwt.encode(payload, config.JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc))


def get_current_user(authorization: str | None = Header(default=None),
                     db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    try:
        claims = decode_token(authorization.removeprefix("Bearer ").strip())
    except AuthError:
        raise HTTPException(401, "Invalid or expired token")
    try:
        user = db.get(User, int(claims["sub"]))
    except (KeyError, ValueError):
        raise HTTPException(401, "Invalid token claims")
    if not user or not user.is_active:
        raise HTTPException(401, "User not found or inactive")
    if "org_id" not in claims:
        raise HTTPException(401, "Stale token — please log in again")
    if claims["org_id"] != user.org_id:
        raise HTTPException(401, "Token org mismatch")
    from .context import set_current_org
    set_current_org(user.org_id)
    return user


def require_role(*roles: str):
    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "Insufficient permissions")
        return user
    return _dep
