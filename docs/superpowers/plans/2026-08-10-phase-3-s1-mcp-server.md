# Phase 3 (S1) — Aethermind MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose Aethermind's extraction as MCP tools (list/extract/get/correct) via a FastMCP server mounted on our FastAPI at `/mcp`, gated by a shared env token.

**Architecture:** A new `backend/app/mcp_server.py` defines testable impl functions (`*_impl(db, ...)`) that reuse the existing services/pipeline, thin `@mcp.tool()` wrappers that open their own `SessionLocal` and run as a fixed service principal, an ASGI token-auth wrapper, and `mcp_asgi_app()`. `main.py` mounts it at `/mcp` only when `MCP_API_TOKEN` is set.

**Tech Stack:** FastMCP (official `mcp` SDK), FastAPI, SQLAlchemy, existing `run_pipeline`.

## Global Constraints

- Python 3.13; backend venv at `backend/.venv` — `source backend/.venv/bin/activate`; run pytest from `backend/`.
- **VERIFIED-COMPATIBLE, DO NOT BUMP:** `mcp==1.9.4`, `sse-starlette==2.1.3`. Newer `mcp` (≥1.16 / 2.x) or `sse-starlette` (≥3) pull **starlette 1.x**, which is incompatible with the pinned `fastapi==0.115.6` (needs starlette <0.42) and breaks the whole app. Pin both exactly.
- Tests run **offline**: no MCP client handshake, no network, no LLM. Async via `asyncio.run` (no `pytest-asyncio`).
- MCP server is **mounted only when `config.MCP_API_TOKEN`** is set (default `""` → not mounted → `/mcp` 404).
- Tools run as a single **service principal**: `id=None`, `email="mcp-service"`, `role=config.MCP_SERVICE_ROLE` (default `"reviewer"`). MCP mutations are audit-stamped with that email.
- Reuse existing code: `services.available_schemas/schema_for/log`, `agents.pipeline.run_pipeline`, `storage.get_storage`, `database.SessionLocal`, models. **`mcp_server.py` must NOT import `main`** (avoid an import cycle — `main` imports `mcp_server`).
- Document input is **base64** in the tool arg (no server-side URL fetch → no SSRF).
- Keep backend **105** / frontend **26** tests green.
- FastMCP API (verified on 1.9.4): `from mcp.server.fastmcp import FastMCP`; `FastMCP(name, stateless_http=True)`; `@mcp.tool()` on functions; `mcp.streamable_http_app()` → a Starlette ASGI app.

## File Structure

- `backend/app/mcp_server.py` — **new**: impl functions, service principal, tool registration (`build_mcp`), token-auth ASGI wrapper, `mcp_asgi_app()`.
- `backend/app/config.py` — add `MCP_API_TOKEN`, `MCP_SERVICE_ROLE`.
- `backend/app/main.py` — `mount_mcp(app)` helper, called at module load (mounts only if token set).
- `backend/requirements.txt` — add the two pinned deps.
- Tests: `backend/tests/test_phase3_mcp_auth.py`, `backend/tests/test_phase3_mcp_tools.py`, `backend/tests/test_phase3_mcp_mount.py`.
- Docs: `.env.example`, `docs/deployment.md`, `docs/roadmap.md`.

---

## Task 1: Dependencies, config, service principal + import smoke test

**Files:**
- Modify: `backend/requirements.txt`, `backend/app/config.py`
- Create: `backend/app/mcp_server.py` (initial: imports + `build_service_principal`), `backend/tests/test_phase3_mcp_smoke.py`

**Interfaces:**
- Produces: `mcp_server.build_service_principal() -> SimpleNamespace(id=None, email="mcp-service", role=config.MCP_SERVICE_ROLE)`; config `MCP_API_TOKEN`, `MCP_SERVICE_ROLE`.

- [ ] **Step 1: Pin deps** — append to `backend/requirements.txt`:
```
mcp==1.9.4
sse-starlette==2.1.3
```
Then `pip install -r requirements.txt` in the venv. (These are already installed from the planning de-risk; this records them.)

- [ ] **Step 2: Add config** — append to `backend/app/config.py`:
```python
MCP_API_TOKEN = os.getenv("MCP_API_TOKEN", "")          # empty ⇒ MCP server disabled
MCP_SERVICE_ROLE = os.getenv("MCP_SERVICE_ROLE", "reviewer")
```

- [ ] **Step 3: Write the failing test** — `backend/tests/test_phase3_mcp_smoke.py`:
```python
def test_fastmcp_importable_and_builds():
    from mcp.server.fastmcp import FastMCP
    s = FastMCP("probe", stateless_http=True)
    app = s.streamable_http_app()
    assert app is not None


def test_service_principal_shape(monkeypatch):
    from app import config, mcp_server
    monkeypatch.setattr(config, "MCP_SERVICE_ROLE", "reviewer")
    p = mcp_server.build_service_principal()
    assert p.id is None and p.email == "mcp-service" and p.role == "reviewer"
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/test_phase3_mcp_smoke.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.mcp_server'`

- [ ] **Step 5: Create `backend/app/mcp_server.py`** (initial):
```python
from types import SimpleNamespace
from . import config


def build_service_principal() -> SimpleNamespace:
    """The fixed identity all MCP tool calls act as (for audit stamping)."""
    return SimpleNamespace(id=None, email="mcp-service", role=config.MCP_SERVICE_ROLE)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_phase3_mcp_smoke.py -v`
Expected: PASS (2 tests)

- [ ] **Step 7: Run full suite + commit**

Run: `pytest -q` → green (105 + 2).
```bash
git add backend/requirements.txt backend/app/config.py backend/app/mcp_server.py backend/tests/test_phase3_mcp_smoke.py
git commit -m "feat: MCP deps (mcp 1.9.4 + sse-starlette 2.1.3), config, service principal"
```

---

## Task 2: Token-auth ASGI wrapper

**Files:**
- Modify: `backend/app/mcp_server.py`
- Test: `backend/tests/test_phase3_mcp_auth.py`

**Interfaces:**
- Produces: `class TokenAuthASGI` — wraps an inner ASGI app; if `token` is set, requires `Authorization: Bearer <token>` (constant-time) on HTTP requests, else returns 401 (plain JSON) without calling the inner app.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_phase3_mcp_auth.py`:
```python
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from app.mcp_server import TokenAuthASGI


def _inner():
    return Starlette(routes=[Route("/", lambda r: PlainTextResponse("ok"))])


def test_missing_token_is_401():
    client = TestClient(TokenAuthASGI(_inner(), token="secret"))
    assert client.get("/").status_code == 401


def test_wrong_token_is_401():
    client = TestClient(TokenAuthASGI(_inner(), token="secret"))
    assert client.get("/", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_correct_token_passes_through():
    client = TestClient(TokenAuthASGI(_inner(), token="secret"))
    r = client.get("/", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200 and r.text == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase3_mcp_auth.py -v`
Expected: FAIL — `ImportError: cannot import name 'TokenAuthASGI'`

- [ ] **Step 3: Implement** — append to `backend/app/mcp_server.py`:
```python
import secrets


class TokenAuthASGI:
    """ASGI middleware: require a Bearer token on HTTP requests before delegating."""
    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send); return
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode()
        presented = auth[7:] if auth.startswith("Bearer ") else ""
        if not (presented and secrets.compare_digest(presented, self.token)):
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
            return
        await self.app(scope, receive, send)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_phase3_mcp_auth.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**
```bash
git add backend/app/mcp_server.py backend/tests/test_phase3_mcp_auth.py
git commit -m "feat: MCP token-auth ASGI wrapper"
```

---

## Task 3: Read/correct tool impls (list / get / submit_correction)

**Files:**
- Modify: `backend/app/mcp_server.py`
- Test: `backend/tests/test_phase3_mcp_tools.py`

**Interfaces:**
- Produces (all take an explicit `db` for testability):
  - `list_types_impl(db) -> list[dict]` → `[{"key","name"}]`
  - `_doc_result(doc) -> dict` → `{document_id, status, confidence, document_type, fields:[{field_name,field_value,confidence,grounded}], anomalies, pipeline_trace}`
  - `get_document_impl(db, document_id: int) -> dict` (raises `ValueError` if missing)
  - `submit_correction_impl(db, document_id: int, field_name: str, value: str, actor) -> dict` (raises `ValueError` if field missing; sets `edited_by_user=True`, audits, commits)

- [ ] **Step 1: Write the failing test** — `backend/tests/test_phase3_mcp_tools.py`:
```python
import pytest
from app import mcp_server
from app.models import Document, ExtractedField, AuditLog


def test_list_types_impl(db_session):
    out = mcp_server.list_types_impl(db_session)
    keys = {t["key"] for t in out}
    assert "invoice" in keys and all("name" in t for t in out)


def test_get_document_impl_roundtrip(db_session):
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x", status="processed", confidence=0.9)
    db_session.add(doc); db_session.flush()
    db_session.add(ExtractedField(document_id=doc.id, field_name="total", field_value="500",
                                  original_value="500", confidence=0.95, grounded="grounded"))
    db_session.commit()
    out = mcp_server.get_document_impl(db_session, doc.id)
    assert out["document_id"] == doc.id and out["fields"][0]["field_value"] == "500"


def test_get_document_impl_missing_raises(db_session):
    with pytest.raises(ValueError):
        mcp_server.get_document_impl(db_session, 99999)


def test_submit_correction_impl_updates_and_audits(db_session):
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x", status="review_required")
    db_session.add(doc); db_session.flush()
    db_session.add(ExtractedField(document_id=doc.id, field_name="total", field_value="500",
                                  original_value="500", confidence=0.9))
    db_session.commit()
    actor = mcp_server.build_service_principal()
    out = mcp_server.submit_correction_impl(db_session, doc.id, "total", "600", actor)
    assert out["field_value"] == "600" and out["edited_by_user"] is True
    f = db_session.query(ExtractedField).filter_by(document_id=doc.id, field_name="total").first()
    assert f.field_value == "600" and f.original_value == "500"
    assert db_session.query(AuditLog).filter_by(document_id=doc.id, actor_email="mcp-service").count() == 1


def test_submit_correction_impl_missing_field_raises(db_session):
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x")
    db_session.add(doc); db_session.commit()
    with pytest.raises(ValueError):
        mcp_server.submit_correction_impl(db_session, doc.id, "nope", "x", mcp_server.build_service_principal())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase3_mcp_tools.py -v`
Expected: FAIL — impl functions not defined.

- [ ] **Step 3: Implement** — append to `backend/app/mcp_server.py` (add imports at top of file: `from .services import available_schemas, schema_for, log`; `from .models import Document, ExtractedField`):
```python
def list_types_impl(db) -> list[dict]:
    return [{"key": s["key"], "name": s["name"]} for s in available_schemas(db)]


def _doc_result(doc: "Document") -> dict:
    return {
        "document_id": doc.id, "status": doc.status, "confidence": doc.confidence,
        "document_type": doc.document_type,
        "fields": [{"field_name": f.field_name, "field_value": f.field_value,
                    "confidence": f.confidence, "grounded": f.grounded}
                   for f in doc.extracted_fields],
        "anomalies": doc.anomalies, "pipeline_trace": doc.pipeline_trace,
    }


def get_document_impl(db, document_id: int) -> dict:
    doc = db.get(Document, document_id)
    if not doc:
        raise ValueError(f"Document {document_id} not found")
    return _doc_result(doc)


def submit_correction_impl(db, document_id: int, field_name: str, value: str, actor) -> dict:
    field = db.query(ExtractedField).filter_by(document_id=document_id, field_name=field_name).first()
    if not field:
        raise ValueError(f"Field '{field_name}' not found on document {document_id}")
    field.field_value = value
    field.edited_by_user = True
    log(db, document_id, "Edited", f"MCP correction: {field_name}", actor=actor)
    db.commit()
    return {"field_name": field.field_name, "field_value": field.field_value,
            "edited_by_user": field.edited_by_user}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_phase3_mcp_tools.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**
```bash
git add backend/app/mcp_server.py backend/tests/test_phase3_mcp_tools.py
git commit -m "feat: MCP read/correct tool impls (list, get_document, submit_correction)"
```

---

## Task 4: `extract_document` impl

**Files:**
- Modify: `backend/app/mcp_server.py`
- Test: `backend/tests/test_phase3_mcp_tools.py` (append)

**Interfaces:**
- Consumes: `storage.get_storage`, `agents.pipeline.run_pipeline`, `schema_for`, `_doc_result`.
- Produces: `async def extract_document_impl(db, file_base64: str, filename: str, document_type: str, actor) -> dict` — validates, decodes, stores, creates a `Document`, `await run_pipeline`, returns `_doc_result`.

- [ ] **Step 1: Write the failing test** — append to `backend/tests/test_phase3_mcp_tools.py`:
```python
import asyncio, base64


def test_extract_document_impl_happy(db_session, monkeypatch):
    from app import mcp_server, storage
    from app.agents import pipeline as pl

    async def fake_run(db, doc, hint, actor=None):
        doc.status = "processed"; doc.confidence = 0.9
        db.add(ExtractedField(document_id=doc.id, field_name="total", field_value="500",
                              original_value="500", confidence=0.95, grounded="grounded"))
        db.commit(); db.refresh(doc); return doc

    monkeypatch.setattr(mcp_server, "run_pipeline", fake_run)
    monkeypatch.setattr(storage, "get_storage",
                        lambda: type("S", (), {"save": lambda self, k, d: k})())
    actor = mcp_server.build_service_principal()
    out = asyncio.run(mcp_server.extract_document_impl(
        db_session, base64.b64encode(b"%PDF-1.4").decode(), "a.pdf", "auto", actor))
    assert out["status"] == "processed" and out["fields"][0]["field_value"] == "500"


def test_extract_document_impl_bad_base64(db_session):
    from app import mcp_server
    with pytest.raises(ValueError):
        asyncio.run(mcp_server.extract_document_impl(
            db_session, "!!!not-base64!!!", "a.pdf", "auto", mcp_server.build_service_principal()))


def test_extract_document_impl_bad_suffix(db_session):
    from app import mcp_server
    with pytest.raises(ValueError):
        asyncio.run(mcp_server.extract_document_impl(
            db_session, base64.b64encode(b"x").decode(), "a.exe", "auto", mcp_server.build_service_principal()))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase3_mcp_tools.py -k extract -v`
Expected: FAIL — `extract_document_impl` not defined.

- [ ] **Step 3: Implement** — append to `backend/app/mcp_server.py` (add imports: `import base64, binascii`; `from datetime import datetime, timezone`; `from pathlib import Path`; `from . import storage`; `from .agents.pipeline import run_pipeline`):
```python
_ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg"}


async def extract_document_impl(db, file_base64: str, filename: str,
                                document_type: str, actor) -> dict:
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise ValueError(f"Unsupported file type '{suffix}' (allowed: PDF, PNG, JPEG)")
    try:
        data = base64.b64decode(file_base64, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("file_base64 is not valid base64")
    hint = "invoice" if document_type == "auto" else document_type
    schema_for(db, hint)  # raises ValueError on unknown type
    key = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{Path(filename).name}"
    storage.get_storage().save(key, data)
    doc = Document(filename=filename, document_type=hint, stored_path=key)
    db.add(doc); db.commit(); db.refresh(doc)
    await run_pipeline(db, doc, doc.document_type, actor=actor)
    db.refresh(doc)
    return _doc_result(doc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_phase3_mcp_tools.py -k extract -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run full suite + commit**

Run: `pytest -q` → green.
```bash
git add backend/app/mcp_server.py backend/tests/test_phase3_mcp_tools.py
git commit -m "feat: MCP extract_document impl (base64 → storage → pipeline)"
```

---

## Task 5: Assemble FastMCP + mount on FastAPI

**Files:**
- Modify: `backend/app/mcp_server.py` (`build_mcp`, `mcp_asgi_app`), `backend/app/main.py` (`mount_mcp`)
- Test: `backend/tests/test_phase3_mcp_mount.py`

**Interfaces:**
- Consumes: all impls, `TokenAuthASGI`, `build_service_principal`, `SessionLocal`, `config`.
- Produces:
  - `mcp_server.build_mcp() -> FastMCP` — registers the 4 tools as thin wrappers that open `SessionLocal()`, call the impl with `build_service_principal()` as actor, and close the session.
  - `mcp_server.mcp_asgi_app()` -> ASGI app = `TokenAuthASGI(build_mcp().streamable_http_app(), config.MCP_API_TOKEN)`.
  - `main.mount_mcp(app) -> bool` — if `config.MCP_API_TOKEN`, `app.mount("/mcp", mcp_asgi_app())` and wire the mcp app's lifespan; returns True if mounted.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_phase3_mcp_mount.py`:
```python
from fastapi import FastAPI
from starlette.testclient import TestClient
from app import config, main


def test_mount_mcp_disabled_when_no_token(monkeypatch):
    monkeypatch.setattr(config, "MCP_API_TOKEN", "")
    app = FastAPI()
    assert main.mount_mcp(app) is False
    assert not any(getattr(r, "path", "").startswith("/mcp") for r in app.routes)


def test_mount_mcp_enabled_and_requires_token(monkeypatch):
    monkeypatch.setattr(config, "MCP_API_TOKEN", "secret")
    app = FastAPI()
    assert main.mount_mcp(app) is True
    # a request to the mounted MCP path without the token is rejected
    client = TestClient(app)
    r = client.post("/mcp/", headers={"Content-Type": "application/json"}, json={})
    assert r.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase3_mcp_mount.py -v`
Expected: FAIL — `main.mount_mcp` not defined.

- [ ] **Step 3: Implement `build_mcp` + `mcp_asgi_app`** — append to `backend/app/mcp_server.py`:
```python
from mcp.server.fastmcp import FastMCP
from .database import SessionLocal


def build_mcp() -> FastMCP:
    mcp = FastMCP("Aethermind", stateless_http=True)

    @mcp.tool()
    def list_document_types() -> list[dict]:
        """List the document types (schemas) Aethermind can extract."""
        db = SessionLocal()
        try:
            return list_types_impl(db)
        finally:
            db.close()

    @mcp.tool()
    async def extract_document(file_base64: str, filename: str, document_type: str = "auto") -> dict:
        """Extract structured fields from a base64-encoded document (PDF/PNG/JPEG)."""
        db = SessionLocal()
        try:
            return await extract_document_impl(db, file_base64, filename, document_type,
                                               build_service_principal())
        finally:
            db.close()

    @mcp.tool()
    def get_document(document_id: int) -> dict:
        """Fetch a processed document's fields, confidence, grounding, and status."""
        db = SessionLocal()
        try:
            return get_document_impl(db, document_id)
        finally:
            db.close()

    @mcp.tool()
    def submit_correction(document_id: int, field_name: str, value: str) -> dict:
        """Correct a single extracted field's value (human-in-the-loop)."""
        db = SessionLocal()
        try:
            return submit_correction_impl(db, document_id, field_name, value,
                                          build_service_principal())
        finally:
            db.close()

    return mcp


def mcp_asgi_app():
    return TokenAuthASGI(build_mcp().streamable_http_app(), config.MCP_API_TOKEN)
```

- [ ] **Step 4: Implement `mount_mcp` in `backend/app/main.py`** — add near the end of the module (import `mcp_server` at top: `from . import mcp_server`):
```python
def mount_mcp(app) -> bool:
    """Mount the MCP server at /mcp only when a token is configured."""
    if not config.MCP_API_TOKEN:
        return False
    mcp_app = mcp_server.build_mcp().streamable_http_app()
    from .mcp_server import TokenAuthASGI
    app.mount("/mcp", TokenAuthASGI(mcp_app, config.MCP_API_TOKEN))
    # FastMCP's streamable HTTP session manager runs via the sub-app's lifespan;
    # attach it so the mounted app starts correctly.
    prev = app.router.lifespan_context
    import contextlib
    @contextlib.asynccontextmanager
    async def _combined(a):
        async with mcp_app.router.lifespan_context(mcp_app):
            async with prev(a):
                yield
    app.router.lifespan_context = _combined
    return True

mount_mcp(app)
```
Note: the implementer should verify the lifespan wiring by running `TestClient(app)` as a context manager against a token-enabled app; if `stateless_http=True` needs no session-manager lifespan, the combined-lifespan block can be simplified — keep whatever makes `test_mount_mcp_enabled_and_requires_token` pass with the app starting cleanly.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_phase3_mcp_mount.py -q && pytest -q`
Expected: new mount tests pass; full suite green.

- [ ] **Step 6: Commit**
```bash
git add backend/app/mcp_server.py backend/app/main.py backend/tests/test_phase3_mcp_mount.py
git commit -m "feat: assemble FastMCP tools + mount at /mcp (token-gated, conditional)"
```

---

## Task 6: Docs

**Files:**
- Modify: `.env.example`, `docs/deployment.md`, `docs/roadmap.md`

- [ ] **Step 1: `.env.example`** — add:
```
MCP_API_TOKEN=
MCP_SERVICE_ROLE=reviewer
```

- [ ] **Step 2: `docs/deployment.md`** — add a "Phase 3 (S1) — MCP server" note: set `MCP_API_TOKEN` (strong value) to enable the MCP server at `POST <backend>/mcp` (Streamable HTTP, Bearer that token); tools = `list_document_types`, `extract_document`, `get_document`, `submit_correction`; runs at `MCP_SERVICE_ROLE`; disabled when the token is unset. Note the pinned `mcp==1.9.4` / `sse-starlette==2.1.3` must not be bumped (starlette conflict with FastAPI 0.115.6).

- [ ] **Step 3: `docs/roadmap.md`** — under Phase 3, mark **S1 (MCP server) shipped** (✅); note S2 (Drive/Gmail ingestion), S3 (webhook/ERP push), S4 (enrichment) remain.

- [ ] **Step 4: Commit**
```bash
git add .env.example docs/deployment.md docs/roadmap.md
git commit -m "docs: Phase 3 S1 MCP server — env, deployment, roadmap"
```

---

## Self-Review

**Spec coverage:**
- FastMCP mounted at `/mcp` → Tasks 5. Token auth wrapper → Task 2 (+ mount uses it). Service principal + audit stamping → Tasks 1/3/5. 4 tools → Tasks 3 (list/get/correct) + 4 (extract). Base64 input, no SSRF → Task 4. Inline `run_pipeline` → Task 4. Mount only when token set → Task 5. Config + pinned deps → Task 1. Docs → Task 6. Injection surface (data-not-instructions) → inherent in tool outputs + noted in docs (Task 6).

**Placeholder scan:** No TBD/TODO. Task 5's lifespan wiring includes an explicit "verify and simplify if stateless needs no session-manager lifespan" instruction with the passing test as the acceptance criterion — a concrete de-risk, not a placeholder (the FastMCP mount lifespan is the one runtime detail that must be confirmed on the machine).

**Type consistency:** impl signatures (`list_types_impl(db)`, `get_document_impl(db, id)`, `submit_correction_impl(db, id, field_name, value, actor)`, `extract_document_impl(db, file_base64, filename, document_type, actor)`), `_doc_result(doc)` shape, `build_service_principal()` (`id/email/role`), `TokenAuthASGI(app, token)`, `build_mcp()`, `mcp_asgi_app()`, `mount_mcp(app)->bool` are consistent across tasks. Deps pinned exactly at the verified `mcp==1.9.4` / `sse-starlette==2.1.3`.
