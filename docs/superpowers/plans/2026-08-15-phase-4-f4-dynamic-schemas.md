# Phase 4 (F4 S1) — Dynamic Schemas (Schema-Author) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the classifier can't confidently match any approved schema, an LLM Schema-Author proposes a fitting schema, the pipeline extracts the triggering document against it immediately, and the draft is persisted as `status="suggested"` for an admin to edit / approve / reject.

**Architecture:** A new isolated module `backend/app/agents/schema_author.py` (LLM propose + validate/normalize + persist) is called from `ClassifierAgent` on low-confidence classification. `SchemaDefinition` gains `status`/`origin_document_id`/`created_at`. `services.available_schemas` is filtered to approved-only (so drafts never reach the classifier / upload selector / MCP tool) while `schema_for` still resolves any key. Admin endpoints + a frontend screen review the drafts.

**Tech Stack:** FastAPI 0.115.6, SQLAlchemy 2.0, Alembic (SQLite dev/test, Neon Postgres prod), OpenAI `responses` API (`CLASSIFIER_MODEL`, default `gpt-4o-mini`), Next.js 15 / React 19 / TypeScript / Tailwind, pytest, vitest + @testing-library/react.

**Spec:** `docs/superpowers/specs/2026-08-15-phase-4-f4-dynamic-schemas-design.md`

## Global Constraints

- Only **approved** schemas (builtins + `status=="approved"` rows) are ever offered to the classifier, the `GET /schemas` upload selector, the `POST /schemas` code paths, or the MCP `list_document_types` tool. Drafts (`status=="suggested"`) are visible only via the admin review endpoints/screen.
- `SchemaDefinition.key` is `unique=True`; the `POST /schemas` duplicate-key check and the author's key generation must both consider **all** keys (builtins ∪ every DB row regardless of status) — never just approved.
- `schema_for(db, key)` must keep resolving a schema by key **regardless of status**.
- The Schema-Author path must never break the pipeline: any failure in `propose_schema`/`persist_suggested` → fall back to today's behavior (force-fit `invoice`/hint). `SCHEMA_AUTHOR_ENABLED=False` → behavior identical to pre-F4.
- Proposed schema guardrails: key `^[a-z0-9_-]+$` (slugified + uniquified); field `name` `^[a-z0-9_]+$` (deduped); `type ∈ {string, number, date, array}` (unknown → `string`); ≥1 and ≤25 fields, else `None`.
- All admin endpoints use `Depends(auth.require_role("admin"))`.
- Schema-review audits log against `origin_document_id` (a non-nullable FK); only log when it is set.
- Do NOT bump any dependency pin (esp. `mcp==1.9.4`, `sse-starlette==2.1.3`, `fastapi==0.115.6`). No new dependencies.
- Migration `0005` chains from the real `0004` revision id **`46ae0d9054f0`**.
- Keep backend **148** / frontend **27** tests green (net new on top).

---

### Task 1: `SchemaDefinition` columns + migration `0005` + review schemas

**Files:**
- Modify: `backend/app/models.py` (SchemaDefinition, ~lines 57-62)
- Create: `backend/alembic/versions/0005_schema_status.py`
- Modify: `backend/app/schemas.py` (add `SchemaEditPayload`, `SuggestedSchemaOut`)
- Test: `backend/tests/test_phase4f4_schemas.py` (new)

**Interfaces:**
- Produces: `SchemaDefinition.status: str` (default `"approved"`), `SchemaDefinition.origin_document_id: int | None`, `SchemaDefinition.created_at: datetime`. Pydantic `SchemaEditPayload {name: str | None = None, fields: list[SchemaFieldDef] | None = None}` and `SuggestedSchemaOut {id, key, name, fields, origin_document_id, created_at}`.

- [ ] **Step 1: Extend the model.** In `backend/app/models.py`, replace the `SchemaDefinition` class body (currently `id`/`key`/`name`/`fields`) with:

```python
class SchemaDefinition(Base):
    __tablename__ = "schema_definitions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    fields: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="approved", server_default="approved")
    origin_document_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=True)
```

(`datetime`, `Integer`, `String`, `DateTime`, `JSON`, `_utcnow` are already imported at the top of `models.py`.)

- [ ] **Step 2: Write the migration.** Create `backend/alembic/versions/0005_schema_status.py`:

```python
"""dynamic schema status

Revision ID: 0005_schema_status
Revises: 46ae0d9054f0
Create Date: 2026-08-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_schema_status"
down_revision: Union[str, None] = "46ae0d9054f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("schema_definitions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("status", sa.String(length=20),
                                      nullable=False, server_default="approved"))
        batch_op.add_column(sa.Column("origin_document_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("created_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("schema_definitions", schema=None) as batch_op:
        batch_op.drop_column("created_at")
        batch_op.drop_column("origin_document_id")
        batch_op.drop_column("status")
```

- [ ] **Step 3: Add the Pydantic review models.** In `backend/app/schemas.py`, after the `SchemaPayload` class (around line 26), add:

```python
class SchemaEditPayload(BaseModel):
    name: str | None = None
    fields: list[SchemaFieldDef] | None = Field(default=None, min_length=1)

class SuggestedSchemaOut(BaseModel):
    id: int
    key: str
    name: str
    fields: list
    origin_document_id: int | None = None
    created_at: datetime | None = None
```

Add `from datetime import datetime` to the top of `schemas.py` (it currently imports only `pydantic` and `typing`).

- [ ] **Step 4: Write the failing test.** Create `backend/tests/test_phase4f4_schemas.py`:

```python
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

BACKEND = Path(__file__).resolve().parents[1]


def test_0005_adds_schema_status_columns(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    cols = {c["name"] for c in inspect(create_engine(url)).get_columns("schema_definitions")}
    assert {"status", "origin_document_id", "created_at"} <= cols
```

- [ ] **Step 5: Run the test.** Run (from `backend/`): `python3 -m pytest tests/test_phase4f4_schemas.py -v`
Expected: PASS (migration chains from `46ae0d9054f0` to head and adds the three columns).

- [ ] **Step 6: Run the full suite.** Run (from `backend/`): `python3 -m pytest -q`
Expected: all green (149 = 148 + 1 new).

- [ ] **Step 7: Commit.**

```bash
git add backend/app/models.py backend/alembic/versions/0005_schema_status.py backend/app/schemas.py backend/tests/test_phase4f4_schemas.py
git commit -m "feat: schema_definitions status/origin/created_at + migration 0005 + review schemas"
```

---

### Task 2: Approved-only `available_schemas` + `all_schema_keys` + global dup-check

**Files:**
- Modify: `backend/app/services.py` (`available_schemas`, ~lines 31-34; add `all_schema_keys`)
- Modify: `backend/app/main.py` (`create_schema`, ~line 148)
- Test: `backend/tests/test_phase4f4_schemas.py` (append)

**Interfaces:**
- Consumes: `SchemaDefinition.status` (Task 1).
- Produces: `services.available_schemas(db)` returns builtins + `status=="approved"` rows only. `services.all_schema_keys(db) -> set[str]` returns builtin keys ∪ every `SchemaDefinition.key`.

- [ ] **Step 1: Write the failing tests.** Append to `backend/tests/test_phase4f4_schemas.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import SchemaDefinition
from app import services


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'svc.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _fields():
    return [{"name": "foo", "label": "Foo", "type": "string", "required": False}]


def test_available_schemas_hides_suggested_but_schema_for_resolves(db):
    db.add(SchemaDefinition(key="widget", name="Widget", fields=_fields(), status="suggested",
                            origin_document_id=1))
    db.commit()
    keys = [s["key"] for s in services.available_schemas(db)]
    assert "widget" not in keys                       # hidden from classifier/selector
    assert services.schema_for(db, "widget")["name"] == "Widget"   # still resolvable


def test_available_schemas_shows_approved(db):
    db.add(SchemaDefinition(key="gadget", name="Gadget", fields=_fields(), status="approved"))
    db.commit()
    assert "gadget" in [s["key"] for s in services.available_schemas(db)]


def test_all_schema_keys_includes_suggested_and_builtins(db):
    db.add(SchemaDefinition(key="widget", name="Widget", fields=_fields(), status="suggested",
                            origin_document_id=1))
    db.commit()
    keys = services.all_schema_keys(db)
    assert "widget" in keys and "invoice" in keys
```

- [ ] **Step 2: Run the tests to verify they fail.** Run (from `backend/`): `python3 -m pytest tests/test_phase4f4_schemas.py -k "available_schemas or all_schema_keys" -v`
Expected: FAIL — `available_schemas` still lists `widget`; `AttributeError: module 'app.services' has no attribute 'all_schema_keys'`.

- [ ] **Step 3: Implement.** In `backend/app/services.py`, replace the `available_schemas` function (lines 31-34) with:

```python
def available_schemas(db: Session):
    """Approved schemas only (builtins + status=='approved' rows). Suggested
    drafts are intentionally excluded from the classifier, the /schemas
    selector, and the MCP list_document_types tool."""
    builtins = [{"key": k, **v} for k, v in SCHEMAS.items()]
    custom = [{"key": s.key, "name": s.name, "fields": s.fields}
              for s in db.query(SchemaDefinition).filter_by(status="approved").all()]
    return builtins + custom


def all_schema_keys(db: Session) -> set[str]:
    """Every schema key — builtins plus every DB row regardless of status.
    Used for uniqueness checks (SchemaDefinition.key is UNIQUE)."""
    return set(SCHEMAS.keys()) | {k for (k,) in db.query(SchemaDefinition.key).all()}
```

- [ ] **Step 4: Repoint the dup-check.** In `backend/app/main.py`, in `create_schema` (line 148), replace:

```python
    if payload.key in [s["key"] for s in available_schemas(db)]: raise HTTPException(409, "Schema key already exists")
```

with:

```python
    if payload.key in all_schema_keys(db): raise HTTPException(409, "Schema key already exists")
```

and update the import on line 15 to include `all_schema_keys`:

```python
from .services import all_schema_keys, available_schemas, log, resolve_review_action, schema_for
```

- [ ] **Step 5: Add the global dup-check test.** Append to `backend/tests/test_phase4f4_schemas.py`:

```python
from fastapi.testclient import TestClient
from app import auth
from app.database import get_db
from app.main import app
from app.models import User


def test_post_schema_409_on_suggested_key_collision(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    session.add(SchemaDefinition(key="widget", name="Widget", fields=_fields(),
                                 status="suggested", origin_document_id=1))
    session.commit()
    admin = User(email="a@t.local", password_hash="x", role="admin", is_active=True)
    session.add(admin); session.commit()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[auth.get_current_user] = lambda: admin
    try:
        r = TestClient(app).post("/schemas", json={"key": "widget", "name": "W2",
                                                   "fields": _fields()})
        assert r.status_code == 409
    finally:
        app.dependency_overrides.clear()
        session.close()
```

- [ ] **Step 6: Run the tests.** Run (from `backend/`): `python3 -m pytest tests/test_phase4f4_schemas.py -q`
Expected: PASS.

- [ ] **Step 7: Run the full suite.** Run (from `backend/`): `python3 -m pytest -q`
Expected: all green (153 = 149 + 4 new).

- [ ] **Step 8: Commit.**

```bash
git add backend/app/services.py backend/app/main.py backend/tests/test_phase4f4_schemas.py
git commit -m "feat: approved-only available_schemas + all_schema_keys + global dup-check"
```

---

### Task 3: `schema_author.py` — `propose_schema` + `persist_suggested`

**Files:**
- Create: `backend/app/agents/schema_author.py`
- Test: `backend/tests/test_phase4f4_author.py` (new)

**Interfaces:**
- Consumes: `services.all_schema_keys` (Task 2), `SchemaDefinition` (Task 1), `config.CLASSIFIER_MODEL`, `services.log`.
- Produces: `async def propose_schema(text: str, existing_keys: set[str]) -> dict | None` returning `{"key": str, "name": str, "fields": [{"name","label","type","required"}]}` or `None`. `def persist_suggested(db, proposal: dict, origin_document_id: int, actor=None) -> SchemaDefinition`.

- [ ] **Step 1: Write the failing tests.** Create `backend/tests/test_phase4f4_author.py`:

```python
import asyncio
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import SchemaDefinition, AuditLog, Document
from app.agents import schema_author


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class FakeResp:
    def __init__(self, text): self.output_text = text


def _stub_client(monkeypatch, output_text):
    class FakeClient:
        def __init__(self, *a, **k): pass
        class responses:
            pass
    fake = FakeClient()

    async def create(**kwargs):
        return FakeResp(output_text)
    fake.responses = type("R", (), {"create": staticmethod(create)})()
    monkeypatch.setattr(schema_author, "_client", lambda: fake)


def test_propose_normalizes_and_returns(monkeypatch):
    _stub_client(monkeypatch, '{"key":"Shipping Manifest","name":"Shipping Manifest",'
                              '"fields":[{"name":"Carrier Name","label":"Carrier","type":"string","required":true},'
                              '{"name":"weird","label":"W","type":"bogus","required":false}]}')
    out = _run(schema_author.propose_schema("some text", set()))
    assert out["key"] == "shipping-manifest"
    names = {f["name"] for f in out["fields"]}
    assert "carrier_name" in names                 # field name normalized
    assert all(f["type"] in {"string", "number", "date", "array"} for f in out["fields"])  # bogus -> string


def test_propose_uniquifies_key(monkeypatch):
    _stub_client(monkeypatch, '{"key":"invoice","name":"Invoice-like",'
                              '"fields":[{"name":"a","label":"A","type":"string","required":false}]}')
    out = _run(schema_author.propose_schema("t", {"invoice"}))
    assert out["key"] == "invoice-2"


def test_propose_none_on_empty_fields(monkeypatch):
    _stub_client(monkeypatch, '{"key":"x","name":"X","fields":[]}')
    assert _run(schema_author.propose_schema("t", set())) is None


def test_propose_none_on_too_many_fields(monkeypatch):
    fields = ",".join('{"name":"f%d","label":"F","type":"string","required":false}' % i
                      for i in range(26))
    _stub_client(monkeypatch, '{"key":"x","name":"X","fields":[%s]}' % fields)
    assert _run(schema_author.propose_schema("t", set())) is None


def test_propose_none_on_exception(monkeypatch):
    def boom():
        raise RuntimeError("no client")
    monkeypatch.setattr(schema_author, "_client", boom)
    assert _run(schema_author.propose_schema("t", set())) is None


def test_persist_suggested_creates_row_and_audit(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'p.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    doc = Document(filename="a.pdf", document_type="unknown", stored_path="a", status="processing")
    db.add(doc); db.commit(); db.refresh(doc)
    proposal = {"key": "manifest", "name": "Manifest",
                "fields": [{"name": "a", "label": "A", "type": "string", "required": False}]}
    row = schema_author.persist_suggested(db, proposal, doc.id)
    db.commit()
    assert row.id and row.status == "suggested" and row.origin_document_id == doc.id
    assert db.query(AuditLog).filter_by(action="Schema suggested").count() == 1
    db.close()
```

- [ ] **Step 2: Run the tests to verify they fail.** Run (from `backend/`): `python3 -m pytest tests/test_phase4f4_author.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents.schema_author'`.

- [ ] **Step 3: Implement.** Create `backend/app/agents/schema_author.py`:

```python
import json
import re
from .. import config, services
from ..models import SchemaDefinition

_ALLOWED_TYPES = {"string", "number", "date", "array"}
_MAX_FIELDS = 25

_RESPONSE_FORMAT = {
    "format": {
        "type": "json_schema", "name": "schema_proposal", "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "name": {"type": "string"},
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "label": {"type": "string"},
                            "type": {"type": "string"},
                            "required": {"type": "boolean"},
                        },
                        "required": ["name", "label", "type", "required"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["key", "name", "fields"],
            "additionalProperties": False,
        },
    }
}


def _client():
    from openai import AsyncOpenAI
    return AsyncOpenAI()


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", (s or "").strip().lower()).strip("-_")


def _field_name(s: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", (s or "").strip().lower()).strip("_")


def _uniquify(key: str, existing: set[str]) -> str:
    if key not in existing:
        return key
    i = 2
    while f"{key}-{i}" in existing:
        i += 1
    return f"{key}-{i}"


def _normalize(data: dict, existing_keys: set[str]) -> dict | None:
    key = _slug(str(data.get("key") or data.get("name") or ""))
    name = str(data.get("name") or "").strip() or (key.replace("-", " ").replace("_", " ").title() if key else "")
    fields, seen = [], set()
    for f in (data.get("fields") or []):
        if not isinstance(f, dict):
            continue
        fname = _field_name(str(f.get("name") or ""))
        if not fname or fname in seen:
            continue
        seen.add(fname)
        ftype = f.get("type") if f.get("type") in _ALLOWED_TYPES else "string"
        label = str(f.get("label") or "").strip() or fname.replace("_", " ").title()
        fields.append({"name": fname, "label": label, "type": ftype,
                       "required": bool(f.get("required", False))})
    if not key or not name or not (1 <= len(fields) <= _MAX_FIELDS):
        return None
    return {"key": _uniquify(key, existing_keys), "name": name, "fields": fields}


async def propose_schema(text: str, existing_keys: set[str]) -> dict | None:
    """Ask the model to propose a schema for an unrecognized document.
    Returns a normalized {key, name, fields} dict, or None on any failure."""
    try:
        client = _client()
        prompt = ("This document did not match any known type. Propose a concise schema to "
                  "extract its key fields. Return JSON {\"key\":\"snake-or-kebab-slug\","
                  "\"name\":\"Human Name\",\"fields\":[{\"name\":\"snake_case\",\"label\":"
                  "\"Human\",\"type\":\"string|number|date|array\",\"required\":bool}]}. "
                  "Use at most 25 fields.\n\nDocument:\n" + (text or "")[:4000])
        resp = await client.responses.create(
            model=config.CLASSIFIER_MODEL,
            input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            text=_RESPONSE_FORMAT)
        return _normalize(json.loads(resp.output_text), existing_keys)
    except Exception:
        return None


def persist_suggested(db, proposal: dict, origin_document_id: int, actor=None) -> SchemaDefinition:
    row = SchemaDefinition(key=proposal["key"], name=proposal["name"], fields=proposal["fields"],
                           status="suggested", origin_document_id=origin_document_id)
    db.add(row)
    db.flush()
    services.log(db, origin_document_id, "Schema suggested", f'{row.key}: {row.name}', actor=actor)
    return row
```

- [ ] **Step 4: Run the tests.** Run (from `backend/`): `python3 -m pytest tests/test_phase4f4_author.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite.** Run (from `backend/`): `python3 -m pytest -q`
Expected: all green (159 = 153 + 6 new).

- [ ] **Step 6: Commit.**

```bash
git add backend/app/agents/schema_author.py backend/tests/test_phase4f4_author.py
git commit -m "feat: schema_author — propose_schema (validate/normalize/dedup) + persist_suggested"
```

---

### Task 4: `ClassifierAgent` hook + `SCHEMA_AUTHOR_ENABLED` config

**Files:**
- Modify: `backend/app/config.py` (add flag after `LEARNING_MAX_HINTS`, ~line 48)
- Modify: `backend/app/agents/classifier.py` (the `body` function inside `ClassifierAgent.run`)
- Test: `backend/tests/test_phase4f4_classifier.py` (new)

**Interfaces:**
- Consumes: `propose_schema`, `persist_suggested` (Task 3); `services.all_schema_keys` (Task 2); `config.SCHEMA_AUTHOR_ENABLED`.
- Produces: on low-confidence classify with the flag on, sets `ctx.document.document_type` to the new draft key and `ctx.schema = {"name", "fields"}` from the draft; otherwise unchanged fallback behavior.

- [ ] **Step 1: Add the config flag.** In `backend/app/config.py`, after the `LEARNING_MAX_HINTS` line (line 48), add:

```python
SCHEMA_AUTHOR_ENABLED = os.getenv("SCHEMA_AUTHOR_ENABLED", "true").lower() in ("1", "true", "yes")
```

- [ ] **Step 2: Write the failing tests.** Create `backend/tests/test_phase4f4_classifier.py`:

```python
import asyncio
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import SchemaDefinition, Document
from app import config
from app.agents import classifier as clf
from app.agents.base import PipelineContext


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def ctx(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'c.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    doc = Document(filename="a.pdf", document_type="unknown", stored_path="a", status="processing")
    db.add(doc); db.commit(); db.refresh(doc)
    c = PipelineContext(db=db, document=doc, hint_type="unknown")
    c.pages = [{"text": "a novel kind of document"}]
    yield c
    db.close()


def test_low_confidence_triggers_author(ctx, monkeypatch):
    monkeypatch.setattr(config, "SCHEMA_AUTHOR_ENABLED", True)

    async def fake_classify(text, keys):
        return None, 0.0
    monkeypatch.setattr(clf, "classify_document", fake_classify)

    async def fake_propose(text, existing):
        return {"key": "manifest", "name": "Manifest",
                "fields": [{"name": "a", "label": "A", "type": "string", "required": False}]}
    monkeypatch.setattr(clf, "propose_schema", fake_propose)

    _run(clf.ClassifierAgent().run(ctx))
    assert ctx.document.document_type == "manifest"
    assert ctx.schema["name"] == "Manifest"
    assert ctx.db.query(SchemaDefinition).filter_by(key="manifest", status="suggested").count() == 1


def test_confident_classify_does_not_author(ctx, monkeypatch):
    monkeypatch.setattr(config, "SCHEMA_AUTHOR_ENABLED", True)

    async def fake_classify(text, keys):
        return "invoice", 0.95
    monkeypatch.setattr(clf, "classify_document", fake_classify)

    called = {"v": False}
    async def fake_propose(text, existing):
        called["v"] = True
        return None
    monkeypatch.setattr(clf, "propose_schema", fake_propose)

    _run(clf.ClassifierAgent().run(ctx))
    assert ctx.document.document_type == "invoice"
    assert called["v"] is False


def test_disabled_flag_never_authors(ctx, monkeypatch):
    monkeypatch.setattr(config, "SCHEMA_AUTHOR_ENABLED", False)

    async def fake_classify(text, keys):
        return None, 0.0
    monkeypatch.setattr(clf, "classify_document", fake_classify)

    called = {"v": False}
    async def fake_propose(text, existing):
        called["v"] = True
        return None
    monkeypatch.setattr(clf, "propose_schema", fake_propose)

    _run(clf.ClassifierAgent().run(ctx))
    assert called["v"] is False
    assert ctx.document.document_type == "invoice"      # unchanged fallback
```

- [ ] **Step 3: Run the tests to verify they fail.** Run (from `backend/`): `python3 -m pytest tests/test_phase4f4_classifier.py -q`
Expected: FAIL — the classifier has no author hook (document_type falls to `invoice`, no `manifest` row).

- [ ] **Step 4: Implement.** In `backend/app/agents/classifier.py`, add the import at the top (after the existing `from .base import ...` line):

```python
from .schema_author import propose_schema, persist_suggested
```

Then replace the `body` coroutine inside `ClassifierAgent.run` with:

```python
        async def body(c: PipelineContext):
            schemas = services.available_schemas(c.db)
            keys = [s["key"] for s in schemas]
            text = c.pages[0]["text"] if c.pages else ""
            key, conf = await classify_document(text, keys)
            if key in keys and conf >= 0.5:
                chosen = key
            elif c.hint_type in keys:
                chosen = c.hint_type
            else:
                chosen = None
            # Schema-Author: no approved schema fit → propose one and extract against it now.
            if chosen is None and config.SCHEMA_AUTHOR_ENABLED:
                proposal = await propose_schema(text, services.all_schema_keys(c.db))
                if proposal:
                    row = persist_suggested(c.db, proposal, c.document.id, actor=c.actor)
                    c.document.document_type = row.key
                    c.schema = {"name": row.name, "fields": row.fields}
                    c._detail = f"authored:{row.key} ({conf:.2f})"
                    return
            if chosen is None:
                chosen = "invoice"
            if key and key != c.hint_type:
                services.log(c.db, c.document.id, "Classified",
                             f"hint={c.hint_type} chosen={chosen} ({conf:.2f})", actor=c.actor)
            c.document.document_type = chosen
            c.schema = services.schema_for(c.db, chosen)
            c._detail = f"{chosen} ({conf:.2f})"
```

(The `result = await timed_stage(...)` and the `result.detail` handling below `body` are unchanged.)

- [ ] **Step 5: Run the tests.** Run (from `backend/`): `python3 -m pytest tests/test_phase4f4_classifier.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full suite.** Run (from `backend/`): `python3 -m pytest -q`
Expected: all green (162 = 159 + 3 new). Existing classifier/pipeline tests still pass (confident-classify path unchanged).

- [ ] **Step 7: Commit.**

```bash
git add backend/app/config.py backend/app/agents/classifier.py backend/tests/test_phase4f4_classifier.py
git commit -m "feat: classifier calls Schema-Author on low-confidence classify (SCHEMA_AUTHOR_ENABLED)"
```

---

### Task 5: Admin review endpoints

**Files:**
- Modify: `backend/app/main.py` (add endpoints near the other `/schemas` routes; update the `schemas` import)
- Test: `backend/tests/test_phase4f4_endpoints.py` (new)

**Interfaces:**
- Consumes: `SchemaDefinition` (Task 1); `SchemaEditPayload`, `SuggestedSchemaOut` (Task 1); `auth.require_role`.
- Produces: `GET /schemas/suggested`, `PATCH /schemas/{schema_id}`, `POST /schemas/{schema_id}/approve`, `DELETE /schemas/{schema_id}`.

- [ ] **Step 1: Write the failing tests.** Create `backend/tests/test_phase4f4_endpoints.py`:

```python
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app import auth
from app.database import Base, get_db
from app.main import app
from app.models import User, SchemaDefinition, Document
from app import services


def _fields():
    return [{"name": "foo", "label": "Foo", "type": "string", "required": False}]


@pytest.fixture
def env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'e.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    doc = Document(filename="a.pdf", document_type="widget", stored_path="a", status="review_required")
    admin = User(email="admin@t.local", password_hash="x", role="admin", is_active=True)
    reviewer = User(email="rev@t.local", password_hash="x", role="reviewer", is_active=True)
    session.add_all([doc, admin, reviewer]); session.commit()
    draft = SchemaDefinition(key="widget", name="Widget", fields=_fields(),
                             status="suggested", origin_document_id=doc.id)
    session.add(draft); session.commit()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[auth.get_current_user] = lambda: admin
    yield {"session": session, "draft_id": draft.id, "admin": admin, "reviewer": reviewer}
    app.dependency_overrides.clear()
    session.close()


def test_list_suggested(env):
    r = TestClient(app).get("/schemas/suggested")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1 and body[0]["key"] == "widget" and body[0]["origin_document_id"]


def test_edit_schema_fields(env):
    new = {"name": "Widget X", "fields": [{"name": "bar", "label": "Bar", "type": "number", "required": True}]}
    r = TestClient(app).patch(f"/schemas/{env['draft_id']}", json=new)
    assert r.status_code == 200 and r.json()["name"] == "Widget X"
    assert r.json()["fields"][0]["name"] == "bar"


def test_edit_rejects_empty_fields(env):
    r = TestClient(app).patch(f"/schemas/{env['draft_id']}", json={"fields": []})
    assert r.status_code == 422


def test_edit_404(env):
    r = TestClient(app).patch("/schemas/9999", json={"name": "z"})
    assert r.status_code == 404


def test_approve_makes_visible_to_classifier(env):
    session = env["session"]
    assert "widget" not in [s["key"] for s in services.available_schemas(session)]
    r = TestClient(app).post(f"/schemas/{env['draft_id']}/approve")
    assert r.status_code == 200
    assert "widget" in [s["key"] for s in services.available_schemas(session)]


def test_approve_404(env):
    assert TestClient(app).post("/schemas/9999/approve").status_code == 404


def test_reject_deletes(env):
    r = TestClient(app).delete(f"/schemas/{env['draft_id']}")
    assert r.status_code == 204
    assert env["session"].get(SchemaDefinition, env["draft_id"]) is None


def test_reject_404(env):
    assert TestClient(app).delete("/schemas/9999").status_code == 404


def test_non_admin_forbidden(env):
    app.dependency_overrides[auth.get_current_user] = lambda: env["reviewer"]
    c = TestClient(app)
    assert c.get("/schemas/suggested").status_code == 403
    assert c.patch(f"/schemas/{env['draft_id']}", json={"name": "z"}).status_code == 403
    assert c.post(f"/schemas/{env['draft_id']}/approve").status_code == 403
    assert c.delete(f"/schemas/{env['draft_id']}").status_code == 403
```

- [ ] **Step 2: Run the tests to verify they fail.** Run (from `backend/`): `python3 -m pytest tests/test_phase4f4_endpoints.py -q`
Expected: FAIL — 404 (routes don't exist).

- [ ] **Step 3: Implement.** In `backend/app/main.py`, update the schema-models import (line 14) to add `SchemaEditPayload, SuggestedSchemaOut`:

```python
from .schemas import DocumentUpdate, LoginRequest, PasswordChange, SchemaEditPayload, SchemaPayload, SuggestedSchemaOut, TokenResponse, UserCreate, UserOut, WebhookCreate, WebhookUpdate, WebhookOut
```

Then add these four endpoints immediately after the existing `POST /schemas` route (`create_schema`, around line 149):

```python
@app.get("/schemas/suggested", response_model=list[SuggestedSchemaOut])
def list_suggested_schemas(db: Session = Depends(get_db),
                           _: User = Depends(auth.require_role("admin"))):
    rows = (db.query(SchemaDefinition).filter_by(status="suggested")
            .order_by(SchemaDefinition.created_at.desc(), SchemaDefinition.id.desc()).all())
    return [{"id": s.id, "key": s.key, "name": s.name, "fields": s.fields,
             "origin_document_id": s.origin_document_id, "created_at": s.created_at} for s in rows]

@app.patch("/schemas/{schema_id}")
def edit_schema(schema_id: int, payload: SchemaEditPayload, db: Session = Depends(get_db),
                user: User = Depends(auth.require_role("admin"))):
    s = db.get(SchemaDefinition, schema_id)
    if not s: raise HTTPException(404, "Schema not found")
    if payload.name is not None: s.name = payload.name
    if payload.fields is not None: s.fields = [f.model_dump() for f in payload.fields]
    if s.origin_document_id: log(db, s.origin_document_id, "Schema edited", s.key, actor=user)
    db.commit(); db.refresh(s)
    return {"id": s.id, "key": s.key, "name": s.name, "fields": s.fields, "status": s.status}

@app.post("/schemas/{schema_id}/approve")
def approve_schema(schema_id: int, db: Session = Depends(get_db),
                   user: User = Depends(auth.require_role("admin"))):
    s = db.get(SchemaDefinition, schema_id)
    if not s: raise HTTPException(404, "Schema not found")
    s.status = "approved"
    if s.origin_document_id: log(db, s.origin_document_id, "Schema approved", s.key, actor=user)
    db.commit(); db.refresh(s)
    return {"id": s.id, "key": s.key, "name": s.name, "status": s.status}

@app.delete("/schemas/{schema_id}", status_code=204)
def reject_schema(schema_id: int, db: Session = Depends(get_db),
                  user: User = Depends(auth.require_role("admin"))):
    s = db.get(SchemaDefinition, schema_id)
    if not s: raise HTTPException(404, "Schema not found")
    if s.origin_document_id: log(db, s.origin_document_id, "Schema rejected", s.key, actor=user)
    db.delete(s); db.commit()
```

Note: FastAPI matches `GET /schemas/suggested` correctly because `{schema_id}` is `int`-typed (the string `"suggested"` never coerces to int), and it is a different HTTP method from the `{schema_id}` PATCH/DELETE routes regardless.

- [ ] **Step 4: Run the tests.** Run (from `backend/`): `python3 -m pytest tests/test_phase4f4_endpoints.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite.** Run (from `backend/`): `python3 -m pytest -q`
Expected: all green (172 = 162 + 10 new).

- [ ] **Step 6: Commit.**

```bash
git add backend/app/main.py backend/tests/test_phase4f4_endpoints.py
git commit -m "feat: admin schema-review endpoints (list suggested / edit / approve / reject)"
```

---

### Task 6: Frontend Suggested Schemas screen + field editor + nav link

**Files:**
- Create: `frontend/app/schemas/suggested/page.tsx`
- Modify: `frontend/app/page.tsx` (admin nav link, line 75)
- Test: `frontend/app/suggested-schemas.test.tsx` (new)

**Interfaces:**
- Consumes: `api`, `me` from `../../lib/api`; the endpoints from Task 5.

- [ ] **Step 1: Implement the page.** Create `frontend/app/schemas/suggested/page.tsx`:

```tsx
"use client";
import { useEffect, useState } from "react";
import { api, me } from "../../../lib/api";

type Field = { name: string; label: string; type: string; required: boolean };
type Draft = { id: number; key: string; name: string; fields: Field[]; origin_document_id: number | null; created_at: string | null };
const TYPES = ["string", "number", "date", "array"];

export default function SuggestedSchemasPage() {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [authorized, setAuthorized] = useState(false);
  const load = () => api("/schemas/suggested").then(setDrafts).catch(() => (window.location.href = "/login"));
  useEffect(() => {
    me().then((u: any) => {
      if (u.role !== "admin") { window.location.href = "/"; return; }
      setAuthorized(true); load();
    }).catch(() => (window.location.href = "/login"));
  }, []);

  function setField(d: Draft, i: number, patch: Partial<Field>) {
    setDrafts((ds) => ds.map((x) => x.id !== d.id ? x
      : { ...x, fields: x.fields.map((f, j) => (j === i ? { ...f, ...patch } : f)) }));
  }
  function addField(d: Draft) {
    setDrafts((ds) => ds.map((x) => x.id !== d.id ? x
      : { ...x, fields: [...x.fields, { name: "new_field", label: "New Field", type: "string", required: false }] }));
  }
  function removeField(d: Draft, i: number) {
    setDrafts((ds) => ds.map((x) => x.id !== d.id ? x : { ...x, fields: x.fields.filter((_, j) => j !== i) }));
  }
  async function save(d: Draft) {
    await api(`/schemas/${d.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: d.name, fields: d.fields }) });
    load();
  }
  async function approve(d: Draft) { await api(`/schemas/${d.id}/approve`, { method: "POST" }); load(); }
  async function reject(d: Draft) { await api(`/schemas/${d.id}`, { method: "DELETE" }); load(); }

  if (!authorized) return null;
  return (
    <div className="mx-auto mt-10 max-w-3xl p-6">
      <h1 className="mb-4 text-xl font-semibold">Suggested Schemas</h1>
      {drafts.length === 0 && <p className="text-sm text-slate-500">No suggested schemas.</p>}
      <ul className="space-y-6">
        {drafts.map((d) => (
          <li key={d.id} className="rounded border p-4">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm font-semibold">{d.name} <code className="text-xs text-slate-500">({d.key})</code></span>
              <span className="text-xs text-slate-500">from doc #{d.origin_document_id ?? "—"}</span>
            </div>
            <ul className="mb-3 space-y-1">
              {d.fields.map((f, i) => (
                <li key={i} className="flex flex-wrap items-center gap-2 text-sm">
                  <input aria-label="Field label" value={f.label}
                         onChange={(e) => setField(d, i, { label: e.target.value })} className="rounded border p-1" />
                  <code className="text-xs text-slate-500">{f.name}</code>
                  <select aria-label="Field type" value={f.type}
                          onChange={(e) => setField(d, i, { type: e.target.value })} className="rounded border p-1">
                    {TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                  <label className="flex items-center gap-1 text-xs">
                    <input type="checkbox" checked={f.required}
                           onChange={(e) => setField(d, i, { required: e.target.checked })} /> required
                  </label>
                  <button onClick={() => removeField(d, i)} className="text-xs text-rose-600 hover:underline">remove</button>
                </li>
              ))}
            </ul>
            <div className="flex gap-2">
              <button onClick={() => addField(d)} className="rounded border px-2 py-1 text-xs">Add field</button>
              <button onClick={() => save(d)} className="rounded border px-2 py-1 text-xs">Save</button>
              <button onClick={() => approve(d)} className="rounded bg-black px-3 py-1 text-xs text-white">Approve</button>
              <button onClick={() => reject(d)} className="rounded border border-rose-300 px-2 py-1 text-xs text-rose-600">Reject</button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 2: Add the nav link.** In `frontend/app/page.tsx` (line 75), immediately after the existing Webhooks admin link expression `{role&&isAdmin(role)&&<a href="/webhooks" className="font-semibold text-slate-700 hover:underline">Webhooks</a>}`, insert a sibling with the same guard and className:

```tsx
{role&&isAdmin(role)&&<a href="/schemas/suggested" className="font-semibold text-slate-700 hover:underline">Suggested Schemas</a>}
```

- [ ] **Step 3: Write the render test.** Create `frontend/app/suggested-schemas.test.tsx`:

```tsx
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import SuggestedSchemasPage from "./schemas/suggested/page";

vi.mock("../lib/api", () => ({
  me: vi.fn(async () => ({ role: "admin" })),
  api: vi.fn(async (path: string) => {
    if (path === "/schemas/suggested")
      return [{ id: 1, key: "manifest", name: "Manifest",
                fields: [{ name: "carrier", label: "Carrier", type: "string", required: false }],
                origin_document_id: 7, created_at: null }];
    return {};
  }),
}));

describe("SuggestedSchemasPage", () => {
  beforeEach(() => vi.clearAllMocks());
  it("lists suggested schema drafts", async () => {
    render(<SuggestedSchemasPage />);
    await waitFor(() => expect(screen.getByText(/Manifest/)).toBeTruthy());
    expect(screen.getByText(/from doc #7/)).toBeTruthy();
  });
});
```

- [ ] **Step 4: Run tests + build.** Run (from `frontend/`): `npx vitest run && npm run build`
Expected: all green (28 = 27 + 1 new); build succeeds; `/schemas/suggested` route generated.

- [ ] **Step 5: Commit.**

```bash
git add frontend/app/schemas/suggested/page.tsx frontend/app/page.tsx frontend/app/suggested-schemas.test.tsx
git commit -m "feat: admin Suggested Schemas screen + field editor + nav link"
```

---

### Task 7: Docs

**Files:**
- Modify: `.env.example`, `docs/deployment.md`, `docs/roadmap.md`

- [ ] **Step 1: `.env.example`.** Add a line documenting the new flag (near the other feature flags such as `LEARNING_ENABLED`):

```
# Schema-Author: auto-propose a schema when a document matches no approved schema (default true)
SCHEMA_AUTHOR_ENABLED=true
```

- [ ] **Step 2: `docs/deployment.md`.** Add a `## Phase 4 (F4 S1) — dynamic schemas` section immediately after the existing `## Phase 4 — Learning loop` section. Cover: when the classifier can't confidently match any approved schema, a Schema-Author LLM proposes a schema, the document is extracted against it immediately and flagged for review, and the schema is stored as a **suggested** draft that only becomes usable for future documents once an admin approves it (Suggested Schemas admin screen or the `/schemas` review API). Note migration `0005` runs via `alembic upgrade head`; new optional env `SCHEMA_AUTHOR_ENABLED` (default `true`); no new dependencies; the feature is inert on documents that classify confidently.

- [ ] **Step 3: `docs/roadmap.md`.** Under `## Phase 4`, mark the dynamic-schemas slice shipped: change the `**F4** Dynamic / AI-suggested schemas (Schema-Author agent)` line to a `✅`-shipped entry (mirror the `### Phase 3 (S1) ✅ Shipped` subsection style, dated 2026-08-15), describing auto-propose-on-low-confidence with admin approve/edit/reject. Note remaining F4 work stays future: an on-demand "suggest schema from this doc" action, automatic de-duplication of near-identical drafts, and deeper confidence work (B4+).

- [ ] **Step 4: Commit.**

```bash
git add .env.example docs/deployment.md docs/roadmap.md
git commit -m "docs: Phase 4 F4 S1 dynamic schemas — env, deployment, roadmap"
```

---

## Self-Review

**Spec coverage:**
- Data model (`status`/`origin_document_id`/`created_at`) + migration `0005` → Task 1. Approved-only `available_schemas` + `all_schema_keys` + global dup-check → Task 2. `propose_schema` (validate/normalize/dedup/guard) + `persist_suggested` → Task 3. Classifier hook + `SCHEMA_AUTHOR_ENABLED` (trigger only on low-confidence; disabled path unchanged) → Task 4. Admin endpoints (list suggested / edit / approve / reject, admin-only, 404s, 422 on empty fields) → Task 5. Frontend screen + field editor + nav link → Task 6. Docs (env/deployment/roadmap) → Task 7. `schema_for` still resolves drafts → tested in Task 2. Draft hidden from classifier until approved → tested in Tasks 2 & 5. MCP `list_document_types` hides drafts (via `available_schemas`) → covered by Task 2's filter.

**Placeholder scan:** No TBD/TODO. Every code step shows complete code; every test step shows the assertions; migration `0005` is hand-written (SQLite batch mode) with an explicit reversible `downgrade`.

**Type consistency:** `propose_schema(text, existing_keys) -> dict|None` and `persist_suggested(db, proposal, origin_document_id, actor=None) -> SchemaDefinition` are defined in Task 3 and consumed identically in Task 4. `available_schemas`/`all_schema_keys`/`schema_for` signatures match across Tasks 2/4. `SchemaEditPayload{name?, fields?}` / `SuggestedSchemaOut{id,key,name,fields,origin_document_id,created_at}` defined in Task 1, imported in Task 5. The `{schema_id}` int path param + `GET /schemas/suggested` ordering are addressed in Task 5. Migration `down_revision` is the real `0004` id `46ae0d9054f0`. Test counts are cumulative (148 → 172 backend, 27 → 28 frontend).
```
