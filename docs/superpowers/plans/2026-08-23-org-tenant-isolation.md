# Org Tenant Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retrofit strict multi-tenant isolation — an Organization model, `org_id` on every tenant table, fail-closed ORM-level query scoping, and a backfill of existing data to one default org — so no account can read across orgs.

**Architecture:** A shared `_TenantMixin` adds `org_id` to all tenant models; a single `do_orm_execute` listener applies `with_loader_criteria(_TenantMixin, org_id == current_org())` — auto-scoping `.query()`, `Session.get()`, and lazy relationship loads. Org context is a ContextVar set from the JWT in `get_current_user` (requests) and threaded explicitly into background paths. `current_org()` raises when unset (fail-closed). Writes stamp `org_id` server-side.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 + Alembic (SQLite dev/test, Neon Postgres prod), pytest.

**Spec:** `docs/superpowers/specs/2026-08-23-org-tenant-isolation-design.md`

## Global Constraints

- **Strict isolation:** no cross-org actor; every user confined to their org; existing bootstrap admin → default org; roles `admin/reviewer/viewer` stay, org-scoped.
- **Mechanism:** one `with_loader_criteria(_TenantMixin, lambda cls: cls.org_id == current_org_id())` listener in `database.py`; a new tenant table is protected simply by inheriting `_TenantMixin`. Never add per-site manual `.filter(org_id=...)` as the primary control.
- **`current_org_id()` is fail-closed** (raises when unset) — but only AFTER the criteria is turned on (Task 4). Before that it returns the value-or-None so incremental stamping doesn't blow up.
- **`org_id` is always server-derived**, never from client input.
- **`User.email` stays GLOBALLY unique** (login is global-by-email). `login` and the `POST /users` email dup-check use `.execution_options(skip_org_filter=True)`. `reset_stuck_processing` (startup, cross-org) also uses `skip_org_filter`. These are the ONLY sanctioned escape hatches — each explicit and audited.
- **Config tables** (`SchemaDefinition`/`WebhookConfig`/`AutoApproveConfig`) become org-scoped with composite `(org_id, key/document_type)` uniques; builtins stay global.
- **Keep the suite green at every task:** because `db_session` in conftest builds tables from the models via `create_all` (not migrations), model changes take effect in tests immediately — update conftest fixtures in the same task that could break them.
- Migrations chain from `0008_document_delivery_flags`; hand-written `op.batch_alter_table` (SQLite+Postgres). No new dependencies. Keep backend **249** / frontend **40** green (net new on top). ⚠️ Before running on prod: pin the real Postgres unique-constraint names (`SELECT conname FROM pg_constraint …`).

---

### Task 1: `Organization` + `_TenantMixin` + `org_id` columns (nullable) + migration 0009 + bootstrap/conftest

**Files:**
- Modify: `backend/app/models.py`, `backend/app/config.py`, `backend/app/services.py` (`create_user`, `bootstrap_admin`)
- Create: `backend/alembic/versions/0009_org_tenancy.py`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/test_org_isolation.py` (new — migration test)

**Interfaces:**
- Produces: `Organization` model; `_TenantMixin` (adds `org_id`, **nullable for now**); `org_id` on `User/Document/ExtractedField/AuditLog/SchemaDefinition/WebhookConfig/AutoApproveConfig`; `config.DEFAULT_ORG_ID`; `create_user(..., org_id)`.

- [ ] **Step 1: Model.** In `backend/app/models.py`, add the mixin + Organization (after the imports/`_utcnow`), and mix `_TenantMixin` into the 7 tenant classes. **`org_id` is nullable in this task** (flipped to NOT NULL in Task 4):

```python
class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

class _TenantMixin:
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
```

- Change each class declaration to inherit the mixin: `class Document(Base, _TenantMixin):`, `class ExtractedField(Base, _TenantMixin):`, `class AuditLog(Base, _TenantMixin):`, `class User(Base, _TenantMixin):`, `class SchemaDefinition(Base, _TenantMixin):`, `class WebhookConfig(Base, _TenantMixin):`, `class AutoApproveConfig(Base, _TenantMixin):`.
- Replace the per-config global uniques with composites:
  - `SchemaDefinition.key`: drop `unique=True` on the column; add `__table_args__ = (UniqueConstraint("org_id", "key", name="uq_schema_definitions_org_id_key"),)`.
  - `WebhookConfig.document_type`: change to `mapped_column(String(80), index=True)` (drop `unique=True`) + `__table_args__ = (Index("ix_webhook_configs_org_document_type", "org_id", "document_type", unique=True),)`.
  - `AutoApproveConfig.document_type`: same pattern → `Index("ix_auto_approve_configs_org_document_type", "org_id", "document_type", unique=True)`.
- Add `UniqueConstraint, Index` to the `sqlalchemy` import line.

- [ ] **Step 2: Config.** In `backend/app/config.py`, add: `DEFAULT_ORG_ID = int(os.getenv("DEFAULT_ORG_ID", "1"))`.

- [ ] **Step 3: bootstrap + create_user.** In `backend/app/services.py`:

```python
def create_user(db: Session, email: str, password: str, role: str, org_id: int | None = None) -> User:
    from .config import DEFAULT_ORG_ID
    user = User(email=email, password_hash=hash_password(password), role=role, is_active=True,
                org_id=org_id if org_id is not None else DEFAULT_ORG_ID)
    db.add(user); db.commit(); db.refresh(user)
    return user

def bootstrap_admin(db: Session) -> None:
    from .config import ADMIN_EMAIL, ADMIN_PASSWORD, DEFAULT_ORG_ID
    from .models import Organization
    if not (ADMIN_EMAIL and ADMIN_PASSWORD):
        return
    if db.get(Organization, DEFAULT_ORG_ID) is None:
        db.add(Organization(id=DEFAULT_ORG_ID, name="Default Organization")); db.commit()
    if db.query(User).filter_by(org_id=DEFAULT_ORG_ID).count() > 0:
        return
    create_user(db, ADMIN_EMAIL, ADMIN_PASSWORD, "admin", org_id=DEFAULT_ORG_ID)
```

- [ ] **Step 4: Migration 0009.** Create `backend/alembic/versions/0009_org_tenancy.py` (nullable add + backfill + composite uniques; NOT NULL flip is Task 4's migration 0010):

```python
"""org tenancy: organizations + org_id (nullable) + backfill + composite uniques

Revision ID: 0009_org_tenancy
Revises: 0008_document_delivery_flags
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0009_org_tenancy"
down_revision: Union[str, None] = "0008_document_delivery_flags"
branch_labels = None
depends_on = None

TENANT_TABLES = ["documents", "users", "extracted_fields", "audit_logs",
                 "schema_definitions", "webhook_configs", "auto_approve_configs"]


def upgrade() -> None:
    op.create_table("organizations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    op.execute("INSERT INTO organizations (id, name, created_at) "
               "VALUES (1, 'Default Organization', CURRENT_TIMESTAMP)")
    for t in TENANT_TABLES:
        with op.batch_alter_table(t, schema=None) as b:
            b.add_column(sa.Column("org_id", sa.Integer(), nullable=True))
            b.create_foreign_key(f"fk_{t}_org_id_organizations", "organizations", ["org_id"], ["id"])
            b.create_index(f"ix_{t}_org_id", ["org_id"])
    for t in TENANT_TABLES:
        op.execute(f"UPDATE {t} SET org_id = 1 WHERE org_id IS NULL")
    # swap global uniques -> composite (org_id, ...). NOTE: pin real Postgres names before prod.
    with op.batch_alter_table("schema_definitions", schema=None) as b:
        b.drop_constraint("schema_definitions_key_key", type_="unique")
        b.create_unique_constraint("uq_schema_definitions_org_id_key", ["org_id", "key"])
    with op.batch_alter_table("webhook_configs", schema=None) as b:
        b.drop_index("ix_webhook_configs_document_type")
        b.create_index("ix_webhook_configs_org_document_type", ["org_id", "document_type"], unique=True)
    with op.batch_alter_table("auto_approve_configs", schema=None) as b:
        b.drop_index("ix_auto_approve_configs_document_type")
        b.create_index("ix_auto_approve_configs_org_document_type", ["org_id", "document_type"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("auto_approve_configs", schema=None) as b:
        b.drop_index("ix_auto_approve_configs_org_document_type")
        b.create_index("ix_auto_approve_configs_document_type", ["document_type"], unique=True)
    with op.batch_alter_table("webhook_configs", schema=None) as b:
        b.drop_index("ix_webhook_configs_org_document_type")
        b.create_index("ix_webhook_configs_document_type", ["document_type"], unique=True)
    with op.batch_alter_table("schema_definitions", schema=None) as b:
        b.drop_constraint("uq_schema_definitions_org_id_key", type_="unique")
        b.create_unique_constraint("schema_definitions_key_key", ["key"])
    for t in TENANT_TABLES:
        with op.batch_alter_table(t, schema=None) as b:
            b.drop_index(f"ix_{t}_org_id")
            b.drop_constraint(f"fk_{t}_org_id_organizations", type_="foreignkey")
            b.drop_column("org_id")
    op.drop_table("organizations")
```

- [ ] **Step 5: conftest default org.** In `backend/tests/conftest.py`, ensure seeded data has an org. In the `db_session` fixture, after `Base.metadata.create_all`, create the default org; in the `client` fixture, give the admin `org_id`:
```python
# in db_session, after create_all + before yielding session used by tests:
from app.models import Organization
session.add(Organization(id=1, name="Default Organization")); session.commit()
# in client fixture: the admin user gets org_id=1
admin = User(email="admin@test.local", password_hash="x", role="admin", is_active=True, org_id=1)
```
(Also add `org_id=1` to any other helper that seeds a `User`/`Document` directly in conftest.)

- [ ] **Step 6: Migration test.** Create `backend/tests/test_org_isolation.py`:
```python
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
BACKEND = Path(__file__).resolve().parents[1]

def test_migration_0009_org_backfill(tmp_path):
    url = f"sqlite:///{tmp_path / 'o.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "0008_document_delivery_flags")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("INSERT INTO users (email,password_hash,role,is_active,created_at) "
                       "VALUES ('a@b.co','x','admin',1,CURRENT_TIMESTAMP)"))
    command.upgrade(cfg, "head")
    insp = inspect(eng)
    assert "organizations" in insp.get_table_names()
    with eng.connect() as c:
        assert c.execute(text("SELECT id FROM organizations")).scalar() == 1
        assert c.execute(text("SELECT org_id FROM users WHERE email='a@b.co'")).scalar() == 1
```

- [ ] **Step 7: Run + commit.** `cd backend && python3 -m pytest -q` → green (org_id nullable, no scoping yet; existing suite unaffected + 1 new). Commit `models.py`, `config.py`, `services.py`, the migration, `conftest.py`, the test.
```bash
git commit -m "feat: Organization model + _TenantMixin org_id (nullable) + migration 0009 + org-aware bootstrap"
```

---

### Task 2: Org context var + write-path stamping + auth JWT claim + escape hatches

**Files:**
- Create: `backend/app/context.py`
- Modify: `backend/app/auth.py`, `backend/app/main.py` (upload, config POSTs, login, /users dup-check), `backend/app/services.py` (`log`, `get_correction_hints`), `backend/app/agents/pipeline.py`, `backend/app/jobs.py`, `backend/app/webhooks.py`, `backend/app/mcp_server.py`, `backend/app/agents/extractor.py`
- Test: `backend/tests/test_org_isolation.py` (append stamping tests)

**Interfaces:**
- Produces: `context.current_org_id() -> int | None` (value-or-None **for now**), `context.set_current_org(org_id)`, `context.reset_org(token)`. JWT carries `org_id`. All INSERTs stamp `org_id`.

- [ ] **Step 1: context.py:**
```python
from contextvars import ContextVar
_current_org: ContextVar[int | None] = ContextVar("current_org_id", default=None)

def set_current_org(org_id: int | None):
    return _current_org.set(org_id)

def reset_org(token) -> None:
    _current_org.reset(token)

def current_org_id() -> int | None:
    # NOTE: hardened to raise-when-unset in Task 4 once the loader-criteria is live.
    return _current_org.get()
```

- [ ] **Step 2: JWT + get_current_user.** In `backend/app/auth.py`: add `"org_id": user.org_id` to the `create_access_token` payload. In `get_current_user`, after resolving `user` (and before returning), set context and validate the claim:
```python
    from .context import set_current_org
    set_current_org(user.org_id)
    # (defense in depth) if claims carry org_id, it must match:
    if "org_id" in claims and claims["org_id"] != user.org_id:
        raise HTTPException(401, "Token org mismatch")
```
Also: a JWT issued before this change lacks `org_id` — treat that as re-login-required is handled by the match check only when present; to be strict, require it: `if "org_id" not in claims: raise HTTPException(401, "Stale token — please log in again")`.
(Note: `get_current_user` itself does `db.get(User, sub)` — in Task 2 there's no criteria yet, so this is unaffected; in Task 4 it will need context set first — it is, because we set it right after the lookup. In Task 4 move `set_current_org` to BEFORE the `db.get(User,...)` and tag that get with `skip_org_filter` OR rely on the user existing in-scope. See Task 4.)

- [ ] **Step 3: login + /users dup-check escape hatch.** In `backend/app/main.py`:
- `login` (line ~108): `user = db.query(User).filter_by(email=payload.email).execution_options(skip_org_filter=True).first()`.
- `POST /users` email dup-check (line ~134): `if db.query(User).filter_by(email=payload.email).execution_options(skip_org_filter=True).first():` (email is globally unique). Then create with the creating admin's org: `create_user(db, payload.email, payload.password, payload.role, org_id=user.org_id)` (the endpoint's `user` dep is the admin).

- [ ] **Step 4: Write-path stamping.**
- `upload` (`main.py` ~212): `doc = Document(filename=..., document_type=..., stored_path=key, org_id=user.org_id)`.
- Config POSTs: `POST /schemas` → `SchemaDefinition(**payload.model_dump(), org_id=user.org_id)`; `POST /webhooks` → add `org_id=user.org_id`; `POST /auto-approve` → add `org_id=user.org_id`. Also change their dup-checks to per-org: schemas `all_schema_keys` already scoped-by-criteria later, but for now add `org_id` filter is unnecessary (criteria handles it in Task 4); leave dup-check queries as-is (they'll be auto-scoped in Task 4).
- `services.log` — stamp org via context:
```python
def log(db, document_id, action, details="", actor=None):
    from .context import current_org_id
    db.add(AuditLog(document_id=document_id, action=action, details=details,
                    actor_id=getattr(actor, "id", None), actor_email=getattr(actor, "email", None),
                    org_id=current_org_id()))
```
- `agents/pipeline.py`: `db.add(ExtractedField(document_id=document.id, original_value=f["field_value"], org_id=document.org_id, **f))`.

- [ ] **Step 5: Background context threading.**
- `jobs.run_pipeline_task(document_id, actor_id=None, org_id=None)`: after `db = SessionLocal()`, `from .context import set_current_org; set_current_org(org_id)`. Its `db.get(Document, ...)` runs before criteria is on (Task 2) — fine; in Task 4 it's scoped by the set org.
- Its caller (`main.py` `process` + the upload→process flow): `background_tasks.add_task(run_pipeline_task, doc.id, user.id, doc.org_id)`.
- `webhooks.deliver_webhook(config_id, document_id, approved_by, org_id=None)`: after `db = SessionLocal()`, `set_current_org(org_id)`. Callers (`update_document`, `pipeline._maybe_auto_approve`) pass `doc.org_id` / `document.org_id`.
- `reset_stuck_processing` (jobs): its `db.query(Document)` must use `.execution_options(skip_org_filter=True)` (cross-org, startup, no context).
- `mcp_server.py`: `build_service_principal()` → add `org_id=config.DEFAULT_ORG_ID` to the SimpleNamespace; in each `@mcp.tool()` wrapper after `db = SessionLocal()` add `from .context import set_current_org; set_current_org(config.DEFAULT_ORG_ID)`; `extract_document_impl` stamps `Document(..., org_id=principal.org_id)`.
- `get_correction_hints(db, org_id, document_type, fields)` — add `org_id` param + `Document.org_id == org_id` to the `.filter(...)`; caller `agents/extractor.py:14`: `services.get_correction_hints(ctx.db, ctx.document.org_id, ctx.document.document_type, fields)`.

- [ ] **Step 6: Stamping tests.** Append tests asserting: `upload` (via `client`) creates a Document with `org_id==1`; a pipeline-created `ExtractedField` and an audit `log()` carry `org_id`. (Use the existing `client`/`db_session` fixtures; context is set via the auth override — see Task 4 note; for Task 2, `current_org_id()` returns None if unset, so stamping may be None in pure-unit contexts — assert org_id on the request-path upload where the admin has org_id and you set context in conftest.)

- [ ] **Step 7: Run + commit.** `python3 -m pytest -q` → green (stamping additive; no filtering yet). Commit all touched files.
```bash
git commit -m "feat: org context var + JWT org claim + write-path org_id stamping + background org threading"
```

---

### Task 3: conftest org-context wiring (prep for fail-closed)

**Files:**
- Modify: `backend/tests/conftest.py`
- Test: verify existing suite still green

**Interfaces:** ensures every test path has org context set, so Task 4's criteria flip doesn't break the suite.

- [ ] **Step 1:** The `client` fixture overrides `auth.get_current_user` with `lambda: admin`, which BYPASSES the real `get_current_user` (so `set_current_org` never runs). Make the override set context too:
```python
def _override_current_user():
    from app.context import set_current_org
    set_current_org(admin.org_id)
    return admin
app.dependency_overrides[auth.get_current_user] = _override_current_user
```
Add an autouse fixture that resets org context between tests:
```python
@pytest.fixture(autouse=True)
def _reset_org_context():
    from app import context
    tok = context.set_current_org(None)
    yield
    context.reset_org(tok)
```
For unit tests that call services/pipeline directly (no client), set context explicitly in the test or the relevant fixture (e.g. the `db_session`-only tests that call `_maybe_auto_approve`, `get_correction_hints`, etc. — set `context.set_current_org(1)` at the top, or pass org explicitly).

- [ ] **Step 2: Run + commit.** `python3 -m pytest -q` → green. Commit conftest.
```bash
git commit -m "test: wire org context into test fixtures (prep for fail-closed scoping)"
```

---

### Task 4: Turn on fail-closed loader-criteria + flip `org_id` NOT NULL (the pivotal task)

**Files:**
- Modify: `backend/app/database.py` (the `do_orm_execute` listener), `backend/app/context.py` (raise-when-unset), `backend/app/models.py` (`org_id` → NOT NULL), `backend/app/auth.py` (order `set_current_org` before the scoped `db.get(User)`)
- Create: `backend/alembic/versions/0010_org_id_not_null.py`
- Test: `backend/tests/test_org_isolation.py` (fail-closed unit test)

- [ ] **Step 1: Loader criteria.** In `backend/app/database.py`, after `SessionLocal = sessionmaker(...)` and `class Base`, register:
```python
from sqlalchemy.orm import with_loader_criteria, Session as _Session
from .models import _TenantMixin  # import after Base is defined; models imports database, so do this lazily
```
Because `models.py` imports from `database.py`, avoid a circular import: register the listener inside a function called at app startup, OR import `_TenantMixin` lazily inside the event handler. Use the lazy pattern:
```python
@event.listens_for(_Session, "do_orm_execute")
def _apply_org_filter(state):
    if not state.is_select:
        return
    if state.execution_options.get("skip_org_filter"):
        return
    from .models import _TenantMixin
    from .context import current_org_id
    state.statement = state.statement.options(
        with_loader_criteria(_TenantMixin, lambda cls: cls.org_id == current_org_id(),
                             include_aliases=True))
```
(Confirm `_TenantMixin` is a superclass of the mapped classes so `with_loader_criteria` matches them — it is, via `class Document(Base, _TenantMixin)`.)

- [ ] **Step 2: Fail-closed context.** In `backend/app/context.py`, change `current_org_id()` to raise when unset:
```python
def current_org_id() -> int:
    v = _current_org.get()
    if v is None:
        raise RuntimeError("org context is not set — refusing to run an unscoped tenant query")
    return v
```

- [ ] **Step 3: get_current_user ordering.** In `auth.get_current_user`, set context BEFORE the `db.get(User, sub)` and make that lookup tolerant (the user's own row): set `set_current_org(claims["org_id"])` first (from the JWT claim, which now always present), then `db.get(User, int(claims["sub"]))` is auto-scoped to that org (the user is in it). Keep the `claims["org_id"] == user.org_id` defense check. Require `org_id` in claims (stale token → 401).

- [ ] **Step 4: NOT NULL.** In `models.py`, change `_TenantMixin.org_id` to `nullable=False`. Create `backend/alembic/versions/0010_org_id_not_null.py`:
```python
revision = "0010_org_id_not_null"; down_revision = "0009_org_tenancy"
from alembic import op; import sqlalchemy as sa
TENANT_TABLES = ["documents","users","extracted_fields","audit_logs",
                 "schema_definitions","webhook_configs","auto_approve_configs"]
def upgrade():
    for t in TENANT_TABLES:
        with op.batch_alter_table(t, schema=None) as b:
            b.alter_column("org_id", existing_type=sa.Integer(), nullable=False)
def downgrade():
    for t in TENANT_TABLES:
        with op.batch_alter_table(t, schema=None) as b:
            b.alter_column("org_id", existing_type=sa.Integer(), nullable=True)
```

- [ ] **Step 5: Fail-closed unit test.** Append to `test_org_isolation.py`: with a `db_session` and org context reset to None, a `db_session.query(Document).all()` (or `.first()`) raises `RuntimeError` (fail-closed). With context set to an org, it returns only that org's rows.

- [ ] **Step 6: Run + commit.** `python3 -m pytest -q` → green (all fixtures set context from Tasks 1–3; all writes stamp org_id). If any existing test now raises "org context not set", it's a real gap — set context in that test/fixture. Commit.
```bash
git commit -m "feat: fail-closed with_loader_criteria org scoping + org_id NOT NULL (migration 0010)"
```

---

### Task 5: Two-org isolation test matrix (the security proof)

**Files:**
- Test: `backend/tests/test_org_isolation.py` (append the matrix)

- [ ] **Step 1: Two-org fixture.** Build a helper creating org 1 + org 2, an admin in each, and — in org 2 — a Document, a custom SchemaDefinition, a WebhookConfig, an AutoApproveConfig, and (for the learning-loop test) an approved doc with an edited ExtractedField. Use a TestClient whose `get_current_user` override returns org-1's admin and sets `set_current_org(1)`.

- [ ] **Step 2: Assert isolation (from org 1, targeting org 2's ids).** Add tests:
```python
# GET /documents lists 0 of org 2's docs; stats/metrics reflect org 1 only
# GET|PUT /document/{b_id} -> 404 ; POST /process/{b_id} -> 404 ; GET /export/{b_id} -> 404
# PATCH/POST(approve)/DELETE /schemas/{b_id} -> 404
# PATCH/DELETE /webhooks/{b_id} -> 404 ; PATCH/DELETE /auto-approve/{b_id} -> 404
# PATCH /users/{b_user_id} -> 404
```
- [ ] **Step 3: Background/leak org-scoping.** Add tests (call services/pipeline directly with `set_current_org(1)`):
```python
# get_correction_hints(db, org_id=1, "invoice", fields) returns ZERO of org 2's corrected values
# should_auto_approve / webhook lookup for an org-1 doc never sees org 2's config
```
- [ ] **Step 4: Run + commit.** `python3 -m pytest -q` → green (expect the full matrix passing). Commit.
```bash
git commit -m "test: two-org isolation matrix — cross-org read/write/export/config/learning-loop all denied"
```

---

### Task 6: Docs

**Files:**
- Modify: `.env.example`, `docs/deployment.md`, `docs/roadmap.md`

- [ ] **Step 1: `.env.example`.** Add `DEFAULT_ORG_ID=1` with a one-line comment.
- [ ] **Step 2: `docs/deployment.md`.** Add an `## Org tenant isolation` section: strict per-org isolation; fail-closed `with_loader_criteria` scoping; migrations `0009` (org_id + backfill + composite uniques) and `0010` (NOT NULL) run via `alembic upgrade head`; **⚠️ pin the real Postgres unique-constraint names before running 0009**; JWTs issued before deploy lack `org_id` → users must re-log-in (fail-closed); MCP is bound to `DEFAULT_ORG_ID` (per-token org binding is a required fast-follow before enabling MCP for a 2nd org); System Health / `/admin/metrics` are now org-scoped; org creation is manual (DB/seed) for now.
- [ ] **Step 3: `docs/roadmap.md`.** Add tenant isolation as shipped under the enterprise track; note remaining enterprise work (B13 retention/deletion; encryption-at-rest; MCP per-token org binding; self-serve org onboarding).
- [ ] **Step 4: Commit.**
```bash
git commit -m "docs: org tenant isolation — env, deployment (prod caveats), roadmap"
```

---

## Self-Review

**Spec coverage:** Organization + `_TenantMixin` + org_id on 7 tables + composite uniques + migration 0009 → Task 1. Context var + JWT claim + write stamping + background threading + get_correction_hints explicit filter → Task 2. Test-fixture context wiring → Task 3. Fail-closed `with_loader_criteria` + `org_id` NOT NULL (0010) + get_current_user ordering → Task 4. Two-org isolation matrix (incl. IDOR 404s, learning-loop, webhook/auto-approve config leaks) → Task 5. Docs (incl. prod constraint-name + stale-JWT + MCP fast-follow caveats) → Task 6.

**Placeholder scan:** No TBD/TODO. The mechanism-critical code (context.py, the `do_orm_execute` listener, `_TenantMixin`, migrations, stamping, get_current_user wiring) is given in full; the ~26 read sites deliberately need NO change (that's the point of the fail-closed criteria) — only the finite write/escape-hatch/background sites are enumerated.

**Type consistency:** `current_org_id()`/`set_current_org()`/`reset_org()` (Task 2, hardened Task 4) used in database.py listener + log() + auth + background. `_TenantMixin.org_id` (Task 1, NOT NULL Task 4) is the single column the criteria filters. `create_user(..., org_id)` (Task 1) called by bootstrap + POST /users (Task 2). `run_pipeline_task(document_id, actor_id, org_id)` / `deliver_webhook(..., org_id)` / `get_correction_hints(db, org_id, document_type, fields)` signatures updated with their callers in Task 2. Migrations chain 0008 → 0009 → 0010. Escape hatches (`skip_org_filter`) confined to login, /users dup-check, reset_stuck_processing. **Sequencing invariant:** criteria (Task 4) turns on only after every write stamps org_id (Task 2) and every test path sets context (Tasks 1–3) — so the suite stays green at each task and is fail-closed at the end.
