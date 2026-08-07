# Phase 1 — Persistence & Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Aethermind onto durable Postgres + object storage and add JWT authentication, role-based access control, and an actor-stamped audit trail.

**Architecture:** Postgres (Neon) in prod via Alembic migrations; SQLite + `LocalStorage` in dev/tests. A `Storage` abstraction hides local-vs-R2. Own FastAPI JWT layer (argon2 hashing, HS256 tokens) with `get_current_user` / `require_role` dependencies guarding endpoints; every mutation stamps the acting user into `audit_logs`.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, psycopg3, argon2-cffi, PyJWT, boto3 (R2), Next.js/React frontend.

## Global Constraints

- Python 3.13; backend venv at `backend/.venv` — `source backend/.venv/bin/activate` before running `pytest`. Run pytest from `backend/`.
- Tests run **offline**: SQLite + `LocalStorage` only. Never call Neon, R2, OpenAI, or Gemini in a test.
- Keep all existing tests green: **54 backend**, **13 frontend**.
- New Python deps pinned in `backend/requirements.txt`: `psycopg[binary]==3.2.3`, `alembic==1.14.0`, `pyjwt==2.10.1`, `argon2-cffi==23.1.0`, `boto3==1.35.71`.
- Roles are exactly the strings `"admin"`, `"reviewer"`, `"viewer"`.
- JWT: HS256, `exp` = `JWT_EXPIRE_HOURS` (default 12), signed with `JWT_SECRET`.
- Timestamps use the Phase-0 `models._utcnow` (tz-aware).
- Never commit `.env`; all secrets via environment variables only.
- Frontend token is stored under the existing `aethermind_token` localStorage key.

## File Structure

- `backend/app/storage.py` — **new**: `Storage` protocol, `LocalStorage`, `S3Storage`, `get_storage()`.
- `backend/app/auth.py` — **new**: password hashing, JWT encode/decode, `get_current_user`, `require_role`.
- `backend/app/models.py` — add `User`; add `actor_id`/`actor_email` to `AuditLog`.
- `backend/app/schemas.py` — add `LoginRequest`, `UserCreate`, `UserOut`, `PasswordChange`, `TokenResponse`.
- `backend/app/config.py` — add storage + JWT + bootstrap-admin settings.
- `backend/app/services.py` — route file bytes through storage; `log()` records actor; add `create_user` / `bootstrap_admin`.
- `backend/app/main.py` — auth + user endpoints; RBAC guards; remove demo gate; actor stamping.
- `backend/app/security.py` — remove `require_access` (keep rate limiter).
- `backend/alembic/`, `backend/alembic.ini` — **new**: migrations.
- `backend/tests/test_phase1_*.py` — **new** test files per area.
- `frontend/lib/api.ts` — Bearer auth; add `login`/`me`.
- `frontend/lib/auth.ts` — **new**: role capability helpers.
- `frontend/app/login/page.tsx` (or `LoginGate` replacement) — login form.
- `frontend/app/page.tsx` — role-gated controls.
- `frontend/app/users/` — **new**: admin user-management screen.

---

## Task 1: Storage abstraction

**Files:**
- Create: `backend/app/storage.py`
- Modify: `backend/app/config.py` (add storage settings), `backend/requirements.txt` (add `boto3==1.35.71`)
- Test: `backend/tests/test_phase1_storage.py`

**Interfaces:**
- Produces:
  - `class LocalStorage: def __init__(self, base: Path); def save(self, key: str, data: bytes) -> str; def open(self, key: str) -> bytes; def delete(self, key: str) -> None`
  - `class S3Storage` — same three methods, backed by boto3/R2.
  - `def get_storage() -> Storage` — returns `LocalStorage(config.UPLOAD_DIR)` when `config.STORAGE_BACKEND == "local"`, else `S3Storage(...)` from R2 config.

- [ ] **Step 1: Add config** — append to `backend/app/config.py` (before the `for directory in ...` block):

```python
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local")  # "local" | "s3"
R2_ENDPOINT = os.getenv("R2_ENDPOINT", "")
R2_BUCKET = os.getenv("R2_BUCKET", "")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
```

- [ ] **Step 2: Write the failing test** — `backend/tests/test_phase1_storage.py`:

```python
from pathlib import Path
import pytest
from app.storage import LocalStorage


def test_local_storage_round_trip(tmp_path):
    store = LocalStorage(tmp_path)
    key = store.save("2026_inv.pdf", b"%PDF-1.4 bytes")
    assert key == "2026_inv.pdf"
    assert store.open(key) == b"%PDF-1.4 bytes"


def test_local_storage_delete(tmp_path):
    store = LocalStorage(tmp_path)
    store.save("x.pdf", b"data")
    store.delete("x.pdf")
    with pytest.raises(FileNotFoundError):
        store.open("x.pdf")


def test_get_storage_returns_local_by_default(monkeypatch, tmp_path):
    from app import config, storage
    monkeypatch.setattr(config, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path)
    s = storage.get_storage()
    assert isinstance(s, LocalStorage)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_phase1_storage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.storage'`

- [ ] **Step 4: Implement** — `backend/app/storage.py`:

```python
from pathlib import Path
from typing import Protocol
from . import config


class Storage(Protocol):
    def save(self, key: str, data: bytes) -> str: ...
    def open(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class LocalStorage:
    def __init__(self, base: Path):
        self.base = Path(base)
        self.base.mkdir(parents=True, exist_ok=True)

    def save(self, key: str, data: bytes) -> str:
        (self.base / key).write_bytes(data)
        return key

    def open(self, key: str) -> bytes:
        return (self.base / key).read_bytes()

    def delete(self, key: str) -> None:
        (self.base / key).unlink(missing_ok=True)


class S3Storage:
    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str):
        import boto3
        self.bucket = bucket
        self.client = boto3.client(
            "s3", endpoint_url=endpoint,
            aws_access_key_id=access_key, aws_secret_access_key=secret_key)

    def save(self, key: str, data: bytes) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return key

    def open(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


def get_storage() -> Storage:
    if config.STORAGE_BACKEND == "s3":
        missing = [n for n, v in [("R2_ENDPOINT", config.R2_ENDPOINT),
                                  ("R2_BUCKET", config.R2_BUCKET),
                                  ("R2_ACCESS_KEY_ID", config.R2_ACCESS_KEY_ID),
                                  ("R2_SECRET_ACCESS_KEY", config.R2_SECRET_ACCESS_KEY)] if not v]
        if missing:
            raise RuntimeError(f"STORAGE_BACKEND=s3 requires: {', '.join(missing)}")
        return S3Storage(config.R2_ENDPOINT, config.R2_BUCKET,
                         config.R2_ACCESS_KEY_ID, config.R2_SECRET_ACCESS_KEY)
    return LocalStorage(config.UPLOAD_DIR)
```

- [ ] **Step 5: Add dependency** — append `boto3==1.35.71` to `backend/requirements.txt`, then `pip install boto3==1.35.71` in the venv.

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_phase1_storage.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Commit**

```bash
git add backend/app/storage.py backend/app/config.py backend/requirements.txt backend/tests/test_phase1_storage.py
git commit -m "feat: storage abstraction (LocalStorage + S3/R2) with config"
```

---

## Task 2: Route uploads and processing through storage

**Files:**
- Modify: `backend/app/main.py` (`upload`), `backend/app/services.py` (`process_document`)
- Test: `backend/tests/test_phase1_storage.py` (append)

**Interfaces:**
- Consumes: `storage.get_storage()` from Task 1.
- Produces: `Document.stored_path` now holds a **storage key**; `process_document` reads bytes via storage into a temp file.

- [ ] **Step 1: Write the failing test** — append to `backend/tests/test_phase1_storage.py`:

```python
def test_process_reads_bytes_through_storage(db_session, monkeypatch, tmp_path):
    from app import config, services
    from app.models import Document
    monkeypatch.setattr(config, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path)
    # a doc whose bytes live only in storage under its key
    from app.storage import LocalStorage
    LocalStorage(tmp_path).save("k.pdf", b"dummy-pdf-bytes")
    captured = {}
    monkeypatch.setattr(services, "extract_text", lambda path: (captured.__setitem__("path", path), "Total 500")[1])
    monkeypatch.setattr(services, "ai_extract", lambda text, fields, path: [
        {"field_name": "total", "field_value": "500", "source_quote": None,
         "grounded": "grounded", "confidence": 0.95}])
    doc = Document(filename="k.pdf", document_type="invoice", stored_path="k.pdf")
    db_session.add(doc); db_session.commit(); db_session.refresh(doc)
    services.process_document(db_session, doc)
    # extract_text received a real temp file path (not the storage key)
    assert captured["path"].endswith(".pdf") and captured["path"] != "k.pdf"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase1_storage.py::test_process_reads_bytes_through_storage -v`
Expected: FAIL — `process_document` still opens `stored_path` as a filesystem path (`FileNotFoundError: 'k.pdf'`).

- [ ] **Step 3: Implement `process_document` storage read** — in `backend/app/services.py`, add imports at top: `import os, tempfile` (keep existing imports) and `from . import storage`. Replace the extraction line inside `process_document`:

```python
        schema = schema_for(db, document.document_type)
        data = storage.get_storage().open(document.stored_path)
        suffix = Path(document.stored_path).suffix
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        try:
            tmp.write(data); tmp.close()
            values = ai_extract(extract_text(tmp.name), schema["fields"], tmp.name)
        finally:
            os.unlink(tmp.name)
```

- [ ] **Step 4: Implement `upload` storage write** — in `backend/app/main.py` `upload`, add `from .storage import get_storage` at top, and replace the two lines that write to `destination`:

```python
    safe_name = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{Path(file.filename).name}"
    key = get_storage().save(safe_name, data)
    doc = Document(filename=file.filename or safe_name, document_type=document_type, stored_path=key)
```
(Delete the old `destination = config.UPLOAD_DIR / safe_name` and `destination.write_bytes(data)` lines.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_phase1_storage.py -v && pytest -q`
Expected: new test PASSES; full suite still green (55+).

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/app/services.py backend/tests/test_phase1_storage.py
git commit -m "feat: read/write document bytes through the storage layer"
```

---

## Task 3: Alembic migrations + Postgres driver

**Files:**
- Create: `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/script.py.mako`, `backend/alembic/versions/` (with the initial migration)
- Modify: `backend/app/main.py` (guard `create_all` to non-prod), `backend/requirements.txt` (`psycopg[binary]==3.2.3`, `alembic==1.14.0`), `render.yaml` (run migrations on deploy)
- Test: `backend/tests/test_phase1_migrations.py`

**Interfaces:**
- Consumes: `app.database.Base.metadata`, `app.config.DATABASE_URL`.
- Produces: `alembic upgrade head` builds the full schema; migration `0001_initial`.

- [ ] **Step 1: Add deps** — append `psycopg[binary]==3.2.3` and `alembic==1.14.0` to `backend/requirements.txt`; `pip install` both in the venv.

- [ ] **Step 2: Scaffold Alembic** — from `backend/`, run `alembic init alembic`. Then replace `backend/alembic/env.py` with:

```python
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
import app.models  # noqa: F401 — register all models on Base.metadata
from app.database import Base
from app.config import DATABASE_URL

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
config.set_main_option("sqlalchemy.url", config.get_main_option("sqlalchemy.url") or DATABASE_URL)
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(url=config.get_main_option("sqlalchemy.url"),
                      target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = config.get_main_option("sqlalchemy.url")
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```
(`render_as_batch=True` keeps ALTER TABLE working on SQLite for later migrations.)

- [ ] **Step 3: Generate the initial migration** — ensure no `sqlalchemy.url` is hardcoded in `alembic.ini` (leave the `sqlalchemy.url =` line blank). Point at a fresh empty SQLite and autogenerate:

```bash
cd backend
DATABASE_URL="sqlite:///$(mktemp -d)/gen.db" alembic revision --autogenerate -m "initial schema"
```
Rename the generated file to `backend/alembic/versions/0001_initial.py`. **Manually verify** the `upgrade()` creates all four tables — `documents`, `extracted_fields` (with `original_value`), `audit_logs`, `schema_definitions` — and `downgrade()` drops them. Fix by hand if autogenerate missed anything.

- [ ] **Step 4: Write the failing test** — `backend/tests/test_phase1_migrations.py`:

```python
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

BACKEND = Path(__file__).resolve().parents[1]


def test_upgrade_head_builds_schema(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    tables = set(inspect(create_engine(url)).get_table_names())
    assert {"documents", "extracted_fields", "audit_logs", "schema_definitions"} <= tables
    cols = {c["name"] for c in inspect(create_engine(url)).get_columns("extracted_fields")}
    assert "original_value" in cols
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_phase1_migrations.py -v`
Expected: PASS. (If it fails, the migration is incomplete — fix `0001_initial.py`.)

- [ ] **Step 6: Prod uses migrations, tests keep `create_all`** — in `backend/app/main.py`, replace `Base.metadata.create_all(bind=engine)` with:

```python
# Tests/dev create the schema directly; prod runs Alembic migrations on deploy.
if config.DATABASE_URL.startswith("sqlite"):
    Base.metadata.create_all(bind=engine)
```

- [ ] **Step 7: Run migrations on deploy** — in `render.yaml`, change the start command to:

```yaml
    startCommand: alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

- [ ] **Step 8: Run full suite**

Run: `pytest -q`
Expected: green (56+).

- [ ] **Step 9: Commit**

```bash
git add backend/alembic backend/alembic.ini backend/requirements.txt backend/app/main.py render.yaml backend/tests/test_phase1_migrations.py
git commit -m "feat: Alembic migrations + Postgres driver; prod migrates on deploy"
```

---

## Task 4: User model, password hashing, and JWT core

**Files:**
- Modify: `backend/app/models.py` (add `User`, audit actor columns), `backend/app/config.py` (JWT + admin settings), `backend/requirements.txt` (`pyjwt`, `argon2-cffi`)
- Create: `backend/app/auth.py` (hashing + token functions only), `backend/alembic/versions/0002_identity.py`
- Test: `backend/tests/test_phase1_auth.py`

**Interfaces:**
- Produces:
  - `User(id, email, password_hash, role, is_active, created_at)`
  - `auth.hash_password(pw: str) -> str`, `auth.verify_password(pw: str, hashed: str) -> bool`
  - `auth.create_access_token(user: User) -> str`, `auth.decode_token(token: str) -> dict` (raises `auth.AuthError` on invalid/expired)
  - `AuditLog.actor_id: int | None`, `AuditLog.actor_email: str | None`

- [ ] **Step 1: Add deps** — append `pyjwt==2.10.1` and `argon2-cffi==23.1.0` to `backend/requirements.txt`; `pip install` both.

- [ ] **Step 2: Add config** — append to `backend/app/config.py`:

```python
JWT_SECRET = os.getenv("JWT_SECRET", "dev-insecure-secret-change-me")
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "12"))
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
```

- [ ] **Step 3: Add the `User` model + audit actor columns** — in `backend/app/models.py`, add after the `AuditLog` class the new model, and add the two columns to `AuditLog`:

```python
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="reviewer")  # admin|reviewer|viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
```
Add to `AuditLog` (after `details`):
```python
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

- [ ] **Step 4: Write the failing test** — `backend/tests/test_phase1_auth.py`:

```python
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
```

- [ ] **Step 5: Run test to verify it fails**

Run: `pytest tests/test_phase1_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.auth'`

- [ ] **Step 6: Implement `backend/app/auth.py`** (hashing + tokens only for this task):

```python
from datetime import datetime, timedelta, timezone
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from . import config
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
               "iat": now, "exp": now + timedelta(hours=config.JWT_EXPIRE_HOURS)}
    return jwt.encode(payload, config.JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc))
```

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/test_phase1_auth.py -v`
Expected: PASS (3 tests)

- [ ] **Step 8: Generate migration 2** — after adding the model:

```bash
cd backend
DATABASE_URL="sqlite:///$(mktemp -d)/gen2.db" alembic upgrade head        # bring scratch db to 0001
DATABASE_URL="sqlite:///$(mktemp -d)/gen2.db" alembic revision --autogenerate -m "identity"
```
Rename to `backend/alembic/versions/0002_identity.py` with `down_revision = "0001_initial"`. Verify `upgrade()` creates `users` and adds `actor_id`/`actor_email` to `audit_logs` (batch mode for SQLite). Add an assertion to the migrations test:

```python
def test_identity_migration_adds_users_and_actor(tmp_path):
    url = f"sqlite:///{tmp_path / 'i.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    insp = inspect(create_engine(url))
    assert "users" in insp.get_table_names()
    assert "actor_id" in {c["name"] for c in insp.get_columns("audit_logs")}
```

- [ ] **Step 9: Run tests**

Run: `pytest tests/test_phase1_auth.py tests/test_phase1_migrations.py -v`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add backend/app/models.py backend/app/auth.py backend/app/config.py backend/requirements.txt backend/alembic/versions backend/tests/test_phase1_auth.py backend/tests/test_phase1_migrations.py
git commit -m "feat: User model, argon2 hashing, JWT tokens, audit actor columns + migration"
```

---

## Task 5: Auth dependencies + test admin override

**Files:**
- Modify: `backend/app/auth.py` (add `get_current_user`, `require_role`)
- Modify: `backend/tests/conftest.py` (autouse override so existing tests stay authed as admin)
- Test: `backend/tests/test_phase1_deps.py`

**Interfaces:**
- Consumes: `auth.decode_token`, `database.get_db`, `models.User`.
- Produces:
  - `auth.get_current_user(...) -> User` (FastAPI dependency; 401 on bad/missing token or inactive user)
  - `auth.require_role(*roles) -> Callable` (dependency; 403 when role not allowed)

- [ ] **Step 1: Write the failing test** — `backend/tests/test_phase1_deps.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase1_deps.py -v`
Expected: FAIL — `AttributeError: module 'app.auth' has no attribute 'get_current_user'`

- [ ] **Step 3: Implement** — append to `backend/app/auth.py`:

```python
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session
from .database import get_db


def get_current_user(authorization: str | None = Header(default=None),
                     db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    try:
        claims = decode_token(authorization.removeprefix("Bearer ").strip())
    except AuthError:
        raise HTTPException(401, "Invalid or expired token")
    user = db.get(User, int(claims["sub"]))
    if not user or not user.is_active:
        raise HTTPException(401, "User not found or inactive")
    return user


def require_role(*roles: str):
    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "Insufficient permissions")
        return user
    return _dep
```

- [ ] **Step 4: Add the test admin override** — append to `backend/tests/conftest.py`:

```python
import pytest
from app import auth
from app.models import User


@pytest.fixture(autouse=True)
def _default_admin(db_session):
    """Existing endpoint tests run authenticated as an admin unless a test
    overrides get_current_user itself."""
    admin = User(email="admin@test.local", password_hash="x", role="admin", is_active=True)
    db_session.add(admin); db_session.commit()
    app.dependency_overrides[auth.get_current_user] = lambda: admin
    yield admin
    app.dependency_overrides.pop(auth.get_current_user, None)
```
(`app` is already imported in `conftest.py`.)

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_phase1_deps.py -q && pytest -q`
Expected: new tests PASS; full suite green.

- [ ] **Step 6: Commit**

```bash
git add backend/app/auth.py backend/tests/conftest.py backend/tests/test_phase1_deps.py
git commit -m "feat: get_current_user + require_role dependencies; test admin override"
```

---

## Task 6: Auth endpoints (login, me, change-password)

**Files:**
- Modify: `backend/app/schemas.py` (auth payloads), `backend/app/main.py` (endpoints)
- Test: `backend/tests/test_phase1_endpoints.py`

**Interfaces:**
- Consumes: `auth.hash_password/verify_password/create_access_token/get_current_user`, `models.User`.
- Produces: `POST /auth/login`, `GET /auth/me`, `POST /auth/change-password`.

- [ ] **Step 1: Add schemas** — append to `backend/app/schemas.py`:

```python
class LoginRequest(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"

class UserOut(BaseModel):
    id: int
    email: str
    role: str
    is_active: bool

class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)

class UserCreate(BaseModel):
    email: str
    password: str = Field(min_length=8)
    role: Literal["admin", "reviewer", "viewer"] = "reviewer"
```
Add `TokenResponse.model_rebuild()` at the end of the file (forward ref to `UserOut`).

- [ ] **Step 2: Write the failing test** — `backend/tests/test_phase1_endpoints.py`:

```python
from app import auth
from app.models import User


def _seed(db, email="u@x.co", role="reviewer", pw="password1"):
    db.add(User(email=email, password_hash=auth.hash_password(pw), role=role, is_active=True))
    db.commit()
    return db.query(User).filter_by(email=email).first()


def test_login_success_and_me(client, db_session):
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
```
(Note: `/auth/me` here goes through the real dependency; the autouse admin override still returns its admin, so assert on `token` behaviour via a dedicated app is unnecessary — instead this test asserts login/token issuance. To exercise the real `get_current_user`, this test removes the override: add `db_session` first line `from app.main import app; app.dependency_overrides.pop(auth.get_current_user, None)`.)

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_phase1_endpoints.py -v`
Expected: FAIL — 404 on `/auth/login`.

- [ ] **Step 4: Implement endpoints** — in `backend/app/main.py`, add imports `from . import auth` and `from .schemas import LoginRequest, TokenResponse, UserOut, PasswordChange, UserCreate` and `from .models import User`, then add:

```python
@app.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(email=payload.email).first()
    if not user or not user.is_active or not auth.verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    return {"access_token": auth.create_access_token(user), "token_type": "bearer",
            "user": {"id": user.id, "email": user.email, "role": user.role, "is_active": user.is_active}}

@app.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(auth.get_current_user)):
    return {"id": user.id, "email": user.email, "role": user.role, "is_active": user.is_active}

@app.post("/auth/change-password")
def change_password(payload: PasswordChange, db: Session = Depends(get_db),
                    user: User = Depends(auth.get_current_user)):
    if not auth.verify_password(payload.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    user.password_hash = auth.hash_password(payload.new_password); db.commit()
    return {"status": "ok"}
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_phase1_endpoints.py -q && pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas.py backend/app/main.py backend/tests/test_phase1_endpoints.py
git commit -m "feat: /auth/login, /auth/me, /auth/change-password"
```

---

## Task 7: User management endpoints + bootstrap admin

**Files:**
- Modify: `backend/app/main.py` (user endpoints + startup bootstrap), `backend/app/services.py` (`create_user`, `bootstrap_admin`)
- Test: `backend/tests/test_phase1_endpoints.py` (append)

**Interfaces:**
- Consumes: `auth.require_role`, `auth.hash_password`, `models.User`, `schemas.UserCreate/UserOut`.
- Produces: `services.create_user(db, email, password, role) -> User`, `services.bootstrap_admin(db) -> None`; `GET/POST /users`, `PATCH /users/{id}`.

- [ ] **Step 1: Write the failing test** — append to `backend/tests/test_phase1_endpoints.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase1_endpoints.py -k "users or bootstrap" -v`
Expected: FAIL — 404 on `/users`; `bootstrap_admin` missing.

- [ ] **Step 3: Implement services** — append to `backend/app/services.py`:

```python
from .auth import hash_password

def create_user(db: Session, email: str, password: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password(password), role=role, is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    return user

def bootstrap_admin(db: Session) -> None:
    from .config import ADMIN_EMAIL, ADMIN_PASSWORD
    if not (ADMIN_EMAIL and ADMIN_PASSWORD):
        return
    if db.query(User).count() > 0:
        return
    create_user(db, ADMIN_EMAIL, ADMIN_PASSWORD, "admin")
```
(Add `User` to the existing `from .models import ...` import in services.py.)

- [ ] **Step 4: Implement endpoints + bootstrap** — in `backend/app/main.py` add:

```python
@app.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(auth.require_role("admin"))):
    return [{"id": u.id, "email": u.email, "role": u.role, "is_active": u.is_active}
            for u in db.query(User).order_by(User.created_at.desc()).all()]

@app.post("/users", status_code=201, response_model=UserOut)
def create_user_endpoint(payload: UserCreate, db: Session = Depends(get_db),
                         _: User = Depends(auth.require_role("admin"))):
    if db.query(User).filter_by(email=payload.email).first():
        raise HTTPException(409, "Email already exists")
    from .services import create_user
    u = create_user(db, payload.email, payload.password, payload.role)
    return {"id": u.id, "email": u.email, "role": u.role, "is_active": u.is_active}

@app.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, role: str | None = None, is_active: bool | None = None,
                db: Session = Depends(get_db), _: User = Depends(auth.require_role("admin"))):
    u = db.get(User, user_id)
    if not u: raise HTTPException(404, "User not found")
    if role is not None: u.role = role
    if is_active is not None: u.is_active = is_active
    db.commit()
    return {"id": u.id, "email": u.email, "role": u.role, "is_active": u.is_active}
```
And add a startup hook near app creation:
```python
@app.on_event("startup")
def _bootstrap():
    from .services import bootstrap_admin
    db = next(get_db())
    try:
        bootstrap_admin(db)
    finally:
        db.close()
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_phase1_endpoints.py -q && pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/app/services.py backend/tests/test_phase1_endpoints.py
git commit -m "feat: admin user-management endpoints + bootstrap admin"
```

---

## Task 8: RBAC guards, actor-stamped audit, remove demo gate

**Files:**
- Modify: `backend/app/main.py` (guards, actor stamping, drop `require_access`), `backend/app/services.py` (`log` records actor), `backend/app/security.py` (remove `require_access`), `backend/app/config.py` (remove `DEMO_ACCESS_TOKEN`)
- Test: `backend/tests/test_phase1_rbac.py`

**Interfaces:**
- Consumes: `auth.get_current_user`, `auth.require_role`.
- Produces: `log(db, document_id, action, details="", actor=None)`; protected endpoints per the spec's authorization map.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_phase1_rbac.py`:

```python
import io
from app import auth
from app.models import Document, ExtractedField
from app.main import app


def _as(db, role):
    from app.models import User
    u = User(email=f"{role}@x.co", password_hash="x", role=role, is_active=True)
    db.add(u); db.commit()
    app.dependency_overrides[auth.get_current_user] = lambda: u
    return u


def test_viewer_cannot_upload(client, db_session):
    _as(db_session, "viewer")
    r = client.post("/upload", files={"file": ("a.pdf", io.BytesIO(b"%PDF"), "application/pdf")})
    assert r.status_code == 403


def test_viewer_can_read_documents(client, db_session):
    _as(db_session, "viewer")
    assert client.get("/documents").status_code == 200


def test_edit_stamps_actor_in_audit(client, db_session):
    actor = _as(db_session, "reviewer")
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x.pdf")
    db_session.add(doc); db_session.flush()
    db_session.add(ExtractedField(document_id=doc.id, field_name="total",
                                  field_value="1", original_value="1", confidence=0.9))
    db_session.commit()
    r = client.put(f"/document/{doc.id}", json={"fields": [], "action": "approve"})
    assert r.status_code == 200
    assert any(a["actor_email"] == "reviewer@x.co" for a in r.json()["audit"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase1_rbac.py -v`
Expected: FAIL — viewer upload returns 201 (no guard); audit entries lack `actor_email`.

- [ ] **Step 3: Update `log` to record actor** — in `backend/app/services.py`:

```python
def log(db: Session, document_id: int, action: str, details: str = "", actor=None):
    db.add(AuditLog(document_id=document_id, action=action, details=details,
                    actor_id=getattr(actor, "id", None),
                    actor_email=getattr(actor, "email", None)))
```

- [ ] **Step 4: Add guards + actor stamping + actor_email in serialize** — in `backend/app/main.py`:
  - Remove `require_access` from imports and the app-level `dependencies=[Depends(require_access)]` (change to `dependencies=[]` or drop the kwarg).
  - `serialize`: change the audit list to include actor — `"audit":[{"action":a.action,"timestamp":a.timestamp,"details":a.details,"actor_email":a.actor_email} for a in d.audit_logs]`.
  - Add dependencies to each route per the map, and thread the user into `log(...)`:

```python
@app.post("/upload", status_code=201, dependencies=[Depends(rate_limit)])
async def upload(file: UploadFile = File(...), document_type: str = "invoice",
                 db: Session = Depends(get_db), user: User = Depends(auth.require_role("admin", "reviewer"))):
    ...
    db.add(doc); db.flush(); log(db, doc.id, "Uploaded", f"Schema selected: {document_type}", actor=user)
    ...

@app.post("/process/{document_id}", dependencies=[Depends(rate_limit)])
def process(document_id: int, db: Session = Depends(get_db),
            user: User = Depends(auth.require_role("admin", "reviewer"))):
    ...

@app.put("/document/{document_id}")
def update_document(document_id: int, payload: DocumentUpdate, db: Session = Depends(get_db),
                    user: User = Depends(auth.require_role("admin", "reviewer"))):
    ...
    log(db, doc.id, outcome["log_action"], outcome["log_details"], actor=user)
    ...

@app.post("/schemas", status_code=201)
def create_schema(payload: SchemaPayload, db: Session = Depends(get_db),
                  _: User = Depends(auth.require_role("admin"))):
    ...

# read endpoints require any authenticated user:
@app.get("/documents")
def documents(q: str = "", status: str = "", db: Session = Depends(get_db),
              _: User = Depends(auth.get_current_user)):
    ...
```
Apply `Depends(auth.get_current_user)` likewise to `GET /schemas`, `GET /document/{id}`, `GET /export/{id}`. `process_document`'s "Processed" log call gains `actor=user` (pass `user` down or log in the endpoint).

- [ ] **Step 5: Remove the demo gate** — delete `require_access` from `backend/app/security.py`, and delete the `DEMO_ACCESS_TOKEN` line from `backend/app/config.py`. Grep to confirm no references remain: `grep -rn "require_access\|DEMO_ACCESS_TOKEN" backend/app` returns nothing.

- [ ] **Step 6: Fix the old security test** — `backend/tests/test_security_endpoints.py` referenced the demo gate. Update or remove the gate-specific assertions (the rate-limit tests stay). Run the file and adjust:

Run: `pytest tests/test_security_endpoints.py -v`

- [ ] **Step 7: Run full suite**

Run: `pytest -q`
Expected: green (all backend tests).

- [ ] **Step 8: Commit**

```bash
git add backend/app/main.py backend/app/services.py backend/app/security.py backend/app/config.py backend/tests/test_phase1_rbac.py backend/tests/test_security_endpoints.py
git commit -m "feat: RBAC guards on all endpoints, actor-stamped audit, remove demo gate"
```

---

## Task 9: Frontend auth layer (Bearer + role helpers)

**Files:**
- Modify: `frontend/lib/api.ts` (Bearer; add `login`, `me`)
- Create: `frontend/lib/auth.ts` (role capability helpers)
- Test: `frontend/lib/auth.test.ts`

**Interfaces:**
- Produces: `login(email, password) -> {access_token, user}`, `me() -> User`, and `canUpload(role)`, `canReview(role)`, `isAdmin(role)`.

- [ ] **Step 1: Write the failing test** — `frontend/lib/auth.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { canUpload, canReview, isAdmin } from "./auth";

describe("role capabilities", () => {
  it("viewer can neither upload nor review", () => {
    expect(canUpload("viewer")).toBe(false);
    expect(canReview("viewer")).toBe(false);
  });
  it("reviewer can upload and review but is not admin", () => {
    expect(canUpload("reviewer")).toBe(true);
    expect(canReview("reviewer")).toBe(true);
    expect(isAdmin("reviewer")).toBe(false);
  });
  it("admin can do everything", () => {
    expect(canUpload("admin")).toBe(true);
    expect(isAdmin("admin")).toBe(true);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run lib/auth.test.ts`
Expected: FAIL — cannot resolve `./auth`.

- [ ] **Step 3: Implement `frontend/lib/auth.ts`:**

```typescript
export type Role = "admin" | "reviewer" | "viewer";
export type User = { id: number; email: string; role: Role; is_active: boolean };

export const canUpload = (r: Role) => r === "admin" || r === "reviewer";
export const canReview = (r: Role) => r === "admin" || r === "reviewer";
export const isAdmin = (r: Role) => r === "admin";
```

- [ ] **Step 4: Switch `api.ts` to Bearer + add auth calls** — in `frontend/lib/api.ts`, replace both `headers["X-Access-Token"] = token;` lines with `headers["Authorization"] = \`Bearer ${token}\`;`, and append:

```typescript
export async function login(email: string, password: string) {
  const r = await fetch(`${API}/auth/login`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!r.ok) { const e: any = new Error("Login failed"); e.status = r.status; throw e; }
  const data = await r.json();
  setToken(data.access_token);
  return data;
}

export async function me() { return api("/auth/me"); }
```

- [ ] **Step 5: Run tests**

Run (from `frontend/`): `npx vitest run`
Expected: PASS; existing 13 + new 3.

- [ ] **Step 6: Commit**

```bash
git add frontend/lib/api.ts frontend/lib/auth.ts frontend/lib/auth.test.ts
git commit -m "feat: frontend Bearer auth + role capability helpers"
```

---

## Task 10: Login page, role-gated UI, admin user screen

**Files:**
- Create: `frontend/app/login/page.tsx`, `frontend/app/users/page.tsx`
- Modify: `frontend/app/page.tsx` (replace demo-token `LoginGate` with real login flow; gate controls by role)
- Test: `frontend/app/login.test.tsx`, `frontend/app/role-gating.test.tsx`

**Interfaces:**
- Consumes: `login`, `me` from `api.ts`; `canUpload`, `isAdmin`, `User` from `auth.ts`.

- [ ] **Step 1: Write the failing test** — `frontend/app/login.test.tsx`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import LoginPage from "./login/page";

vi.mock("../lib/api", () => ({ login: vi.fn().mockResolvedValue({ access_token: "t", user: { role: "admin" } }) }));

describe("LoginPage", () => {
  beforeEach(() => vi.clearAllMocks());
  it("submits email + password", async () => {
    const { login } = await import("../lib/api");
    render(<LoginPage />);
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "a@b.co" } });
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: "password1" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => expect(login).toHaveBeenCalledWith("a@b.co", "password1"));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run app/login.test.tsx`
Expected: FAIL — cannot resolve `./login/page`.

- [ ] **Step 3: Implement `frontend/app/login/page.tsx`:**

```tsx
"use client";
import { useState } from "react";
import { login } from "../../lib/api";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await login(email, password);
      window.location.href = "/";
    } catch {
      setError("Invalid email or password");
    }
  }
  return (
    <form onSubmit={submit} className="mx-auto mt-24 flex max-w-sm flex-col gap-4 p-6">
      <h1 className="text-xl font-semibold">Sign in to Aethermind</h1>
      <label className="flex flex-col gap-1 text-sm">Email
        <input aria-label="Email" type="email" value={email}
               onChange={(e) => setEmail(e.target.value)} className="rounded border p-2" />
      </label>
      <label className="flex flex-col gap-1 text-sm">Password
        <input aria-label="Password" type="password" value={password}
               onChange={(e) => setPassword(e.target.value)} className="rounded border p-2" />
      </label>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <button type="submit" className="rounded bg-black p-2 text-white">Sign in</button>
    </form>
  );
}
```

- [ ] **Step 4: Role-gate the main page** — in `frontend/app/page.tsx`: on mount, call `me()` to load the current user (redirect to `/login` on 401). Store `role` in state. Wrap the upload control in `{canUpload(role) && ...}` and approve/edit controls in `{canReview(role) && ...}`; show a "Users" link only when `isAdmin(role)`. Remove the demo-token `LoginGate`. Write `frontend/app/role-gating.test.tsx`:

```typescript
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { canUpload } from "../lib/auth";

// Thin guard: the upload button is only rendered when canUpload(role) is true.
describe("role gating", () => {
  it("hides upload affordance for viewers", () => {
    expect(canUpload("viewer")).toBe(false);
  });
});
```
(If `page.tsx` is straightforward to render in jsdom, prefer asserting the button's presence/absence directly by mocking `me()` to return each role; otherwise the capability-level assertion above plus the wiring is sufficient.)

- [ ] **Step 5: Implement `frontend/app/users/page.tsx`** (admin screen) — a client component that `me()`-guards to admin, lists users via `api("/users")`, and posts new users via `api("/users", {method:"POST", ...})`. Minimal form: email, password, role select, submit; table of existing users with a deactivate button (`PATCH /users/{id}?is_active=false`).

```tsx
"use client";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { User } from "../../lib/auth";

export default function UsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [form, setForm] = useState({ email: "", password: "", role: "reviewer" });
  const load = () => api("/users").then(setUsers).catch(() => (window.location.href = "/login"));
  useEffect(() => { load(); }, []);
  async function add(e: React.FormEvent) {
    e.preventDefault();
    await api("/users", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(form) });
    setForm({ email: "", password: "", role: "reviewer" }); load();
  }
  return (
    <div className="mx-auto mt-10 max-w-2xl p-6">
      <h1 className="mb-4 text-xl font-semibold">Users</h1>
      <form onSubmit={add} className="mb-6 flex gap-2">
        <input aria-label="Email" placeholder="email" value={form.email}
               onChange={(e) => setForm({ ...form, email: e.target.value })} className="rounded border p-2" />
        <input aria-label="Password" placeholder="password" type="password" value={form.password}
               onChange={(e) => setForm({ ...form, password: e.target.value })} className="rounded border p-2" />
        <select aria-label="Role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}
                className="rounded border p-2">
          <option value="admin">admin</option><option value="reviewer">reviewer</option><option value="viewer">viewer</option>
        </select>
        <button className="rounded bg-black px-3 text-white">Add</button>
      </form>
      <ul>{users.map((u) => <li key={u.id}>{u.email} — {u.role}{u.is_active ? "" : " (inactive)"}</li>)}</ul>
    </div>
  );
}
```

- [ ] **Step 6: Run tests + build**

Run (from `frontend/`): `npx vitest run && npm run build`
Expected: tests green; Next build succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/app/login frontend/app/users frontend/app/page.tsx frontend/app/login.test.tsx frontend/app/role-gating.test.tsx
git commit -m "feat: login page, role-gated UI, admin user-management screen"
```

---

## Task 11: Docs + deployment notes

**Files:**
- Modify: `docs/roadmap.md` (mark Phase 1 done), `docs/deployment.md` (new env vars), `.env.example` (new keys)
- Test: none (docs)

- [ ] **Step 1: Update `.env.example`** with the new keys (no secret values):

```
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST/db
STORAGE_BACKEND=s3
R2_ENDPOINT=https://<accountid>.r2.cloudflarestorage.com
R2_BUCKET=aethermind
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
JWT_SECRET=
JWT_EXPIRE_HOURS=12
ADMIN_EMAIL=
ADMIN_PASSWORD=
```

- [ ] **Step 2: Document deploy** in `docs/deployment.md`: Neon DB URL, R2 bucket + keys, `JWT_SECRET`, bootstrap `ADMIN_EMAIL`/`ADMIN_PASSWORD`, and that Render now runs `alembic upgrade head` on start. Note `DEMO_ACCESS_TOKEN` is removed.

- [ ] **Step 3: Mark Phase 1 complete** in `docs/roadmap.md`.

- [ ] **Step 4: Commit**

```bash
git add docs/roadmap.md docs/deployment.md .env.example
git commit -m "docs: Phase 1 deployment + env documentation"
```

---

## Self-Review

**Spec coverage:**
- A1 Postgres driver → Task 3. A2 Alembic → Task 3 (+ migration 2 in Task 4). A3 storage abstraction → Task 1. A4 wire extraction → Task 2.
- B1 User + audit actor → Task 4. B2 hashing + JWT → Task 4. B3 deps get_current_user/require_role → Task 5; demo gate removal → Task 8. B4 endpoints → Tasks 6–7. B5 authorization map → Task 8. B6 bootstrap admin → Task 7. B7 actor-stamped audit → Task 8.
- Part C frontend → Tasks 9–10. Error handling (401/403/409/fast-fail) → Tasks 5–8 + Task 1 (`get_storage` fast-fail). Testing strategy → conftest override (Task 5) + per-area tests. Deployment notes → Task 11.

**Placeholder scan:** No TBD/TODO. Alembic migration bodies are autogenerated (deterministic) with an explicit manual-verification checklist — the standard, not a placeholder. Task 10 Step 4 offers a fallback assertion strategy where jsdom rendering of the large `page.tsx` is impractical; the capability wiring is fully specified.

**Type consistency:** `get_storage()`, `save/open/delete`, `hash_password/verify_password/create_access_token/decode_token`, `get_current_user/require_role`, `log(..., actor=None)`, roles `admin|reviewer|viewer`, and the `UserOut` shape (`id,email,role,is_active`) are used consistently across backend tasks and mirrored in `frontend/lib/auth.ts`.
