# Phase 3 (S3) — Webhook Push on Approve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a document is approved, POST its structured result to a per-type webhook (async, HMAC-signable), managed by admins.

**Architecture:** A `WebhookConfig` table (per document type) with admin CRUD at `/webhooks`; `backend/app/webhooks.py` builds/signs/delivers the payload in a FastAPI BackgroundTask; `PUT /document/{id}` schedules delivery when the approve action fires. A minimal admin Webhooks screen manages configs.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, `requests` (outbound POST), Next.js frontend.

## Global Constraints

- Python 3.13; backend venv at `backend/.venv` — `source backend/.venv/bin/activate`; run pytest from `backend/`.
- New dep `requests==2.34.2` (already installed transitively; pin it, do not bump).
- Migration `0004` chains from `0003` (`down_revision = 'bfa90b8739d0'`); prod runs `alembic upgrade head` on deploy; tests keep SQLite `create_all`.
- Delivery is **async** (BackgroundTasks), **single attempt**, **10s timeout**, HMAC-SHA256 signed (`X-Aethermind-Signature: sha256=<hex>`) only when the config has a `secret`; `deliver_webhook` opens its own `SessionLocal`, swallows all exceptions, audits the outcome.
- **`WebhookOut` never returns the secret** — it exposes `has_secret: bool`.
- Webhook endpoints are `require_role("admin")`. Push fires **on approve only** (`outcome["status"] == "approved"`).
- Tests run **offline**: `requests.post` is stubbed; TestClient runs BackgroundTasks synchronously so wiring tests assert *dispatch*.
- Call collaborators via their module where tests monkeypatch: `main.deliver_webhook`, `webhooks.requests`, `webhooks.SessionLocal`.
- Keep backend **134** / frontend **26** tests green.

## File Structure

- `backend/app/models.py` — `WebhookConfig`.
- `backend/app/schemas.py` — `WebhookCreate`, `WebhookUpdate`, `WebhookOut`.
- `backend/alembic/versions/0004_webhook_configs.py` — migration.
- `backend/app/webhooks.py` — **new**: `build_payload`, `sign`, `deliver_webhook`.
- `backend/app/main.py` — CRUD endpoints + push-on-approve wiring.
- `backend/requirements.txt` — pin `requests`.
- `frontend/app/webhooks/page.tsx` — admin screen; `frontend/app/page.tsx` — nav link.
- Tests: `backend/tests/test_phase3s3_webhooks.py`, `frontend/app/webhooks.test.tsx`.

---

## Task 1: `WebhookConfig` model + migration + schemas

**Files:**
- Modify: `backend/app/models.py`, `backend/app/schemas.py`
- Create: `backend/alembic/versions/0004_webhook_configs.py`
- Test: `backend/tests/test_phase3s3_webhooks.py`

**Interfaces:**
- Produces: `models.WebhookConfig(id, document_type, url, secret, active, created_at)`; `schemas.WebhookCreate/WebhookUpdate/WebhookOut`.

- [ ] **Step 1: Add the model** — in `backend/app/models.py`, after `SchemaDefinition`:
```python
class WebhookConfig(Base):
    __tablename__ = "webhook_configs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_type: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    url: Mapped[str] = mapped_column(Text)
    secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
```

- [ ] **Step 2: Add schemas** — append to `backend/app/schemas.py`:
```python
class WebhookCreate(BaseModel):
    document_type: str
    url: str
    secret: str | None = None
    active: bool = True

class WebhookUpdate(BaseModel):
    url: str | None = None
    secret: str | None = None
    active: bool | None = None

class WebhookOut(BaseModel):
    id: int
    document_type: str
    url: str
    active: bool
    has_secret: bool
```

- [ ] **Step 3: Generate migration 0004** — from `backend/`, using the `.env`-aside procedure (config's `load_dotenv(override=True)` shadows an exported `DATABASE_URL`):
```bash
mv ../.env ../.env.bak
DB="sqlite:///$(mktemp -d)/gen4.db"
DATABASE_URL="$DB" alembic upgrade head        # bring scratch db to 0003
DATABASE_URL="$DB" alembic revision --autogenerate -m "webhook configs"
mv ../.env.bak ../.env
```
Rename the file to `backend/alembic/versions/0004_webhook_configs.py`. Verify `down_revision = 'bfa90b8739d0'` (autogenerate sets it — do NOT hardcode a filename), `upgrade()` creates `webhook_configs` (with the unique index on `document_type`), `downgrade()` drops it. Confirm `.env` unchanged after restore.

- [ ] **Step 4: Write the failing test** — `backend/tests/test_phase3s3_webhooks.py`:
```python
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

BACKEND = Path(__file__).resolve().parents[1]


def test_migration_adds_webhook_configs(tmp_path):
    url = f"sqlite:///{tmp_path / 'w.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    tables = set(inspect(create_engine(url)).get_table_names())
    assert "webhook_configs" in tables
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_phase3s3_webhooks.py -v`
Expected: PASS. (If it fails, the migration is incomplete — fix `0004`.)

- [ ] **Step 6: Run full suite + commit**

Run: `pytest -q` → green (134 + 1).
```bash
git add backend/app/models.py backend/app/schemas.py backend/alembic/versions/0004_webhook_configs.py backend/tests/test_phase3s3_webhooks.py
git commit -m "feat: WebhookConfig model + migration 0004 + webhook schemas"
```

---

## Task 2: Admin CRUD endpoints

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_phase3s3_webhooks.py` (append)

**Interfaces:**
- Consumes: `WebhookConfig`, `WebhookCreate/Update/Out`, `auth.require_role`.
- Produces: `GET/POST/PATCH/DELETE /webhooks`; helper `_webhook_out(w) -> dict`.

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/test_phase3s3_webhooks.py`:
```python
from app import auth
from app.main import app
from app.models import User, WebhookConfig


def _as(db, role):
    u = User(email=f"{role}@x.co", password_hash="x", role=role, is_active=True)
    db.add(u); db.commit()
    app.dependency_overrides[auth.get_current_user] = lambda: u
    return u


def test_create_list_and_secret_hidden(client, db_session):
    r = client.post("/webhooks", json={"document_type": "invoice", "url": "https://h/x", "secret": "s3cr3t"})
    assert r.status_code == 201
    body = r.json()
    assert body["document_type"] == "invoice" and body["has_secret"] is True
    assert "secret" not in body                      # secret never returned
    listed = client.get("/webhooks").json()
    assert any(w["document_type"] == "invoice" for w in listed)


def test_duplicate_type_409(client, db_session):
    client.post("/webhooks", json={"document_type": "invoice", "url": "https://h/x"})
    dup = client.post("/webhooks", json={"document_type": "invoice", "url": "https://h/y"})
    assert dup.status_code == 409


def test_patch_and_delete(client, db_session):
    wid = client.post("/webhooks", json={"document_type": "invoice", "url": "https://h/x"}).json()["id"]
    assert client.patch(f"/webhooks/{wid}", json={"active": False}).json()["active"] is False
    assert client.delete(f"/webhooks/{wid}").status_code == 204
    assert client.patch(f"/webhooks/{wid}", json={"active": True}).status_code == 404


def test_non_admin_forbidden(client, db_session):
    _as(db_session, "reviewer")
    try:
        assert client.get("/webhooks").status_code == 403
        assert client.post("/webhooks", json={"document_type": "x", "url": "https://h"}).status_code == 403
    finally:
        app.dependency_overrides.pop(auth.get_current_user, None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_phase3s3_webhooks.py -k "create or duplicate or patch or non_admin" -v`
Expected: FAIL — 404/405 (endpoints missing).

- [ ] **Step 3: Implement** — in `backend/app/main.py`, add `WebhookConfig` to the `.models` import and `WebhookCreate, WebhookUpdate, WebhookOut` to the `.schemas` import, then add:
```python
def _webhook_out(w: WebhookConfig) -> dict:
    return {"id": w.id, "document_type": w.document_type, "url": w.url,
            "active": w.active, "has_secret": bool(w.secret)}

@app.get("/webhooks", response_model=list[WebhookOut])
def list_webhooks(db: Session = Depends(get_db), _: User = Depends(auth.require_role("admin"))):
    return [_webhook_out(w) for w in db.query(WebhookConfig).order_by(WebhookConfig.document_type).all()]

@app.post("/webhooks", status_code=201, response_model=WebhookOut)
def create_webhook(payload: WebhookCreate, db: Session = Depends(get_db),
                   _: User = Depends(auth.require_role("admin"))):
    if db.query(WebhookConfig).filter_by(document_type=payload.document_type).first():
        raise HTTPException(409, "A webhook for that document type already exists")
    w = WebhookConfig(**payload.model_dump()); db.add(w); db.commit(); db.refresh(w)
    return _webhook_out(w)

@app.patch("/webhooks/{webhook_id}", response_model=WebhookOut)
def update_webhook(webhook_id: int, payload: WebhookUpdate, db: Session = Depends(get_db),
                   _: User = Depends(auth.require_role("admin"))):
    w = db.get(WebhookConfig, webhook_id)
    if not w: raise HTTPException(404, "Webhook not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(w, k, v)
    db.commit(); db.refresh(w)
    return _webhook_out(w)

@app.delete("/webhooks/{webhook_id}", status_code=204)
def delete_webhook(webhook_id: int, db: Session = Depends(get_db),
                   _: User = Depends(auth.require_role("admin"))):
    w = db.get(WebhookConfig, webhook_id)
    if not w: raise HTTPException(404, "Webhook not found")
    db.delete(w); db.commit()
```

- [ ] **Step 4: Run tests + commit**

Run: `pytest tests/test_phase3s3_webhooks.py -q && pytest -q`
Expected: green.
```bash
git add backend/app/main.py backend/tests/test_phase3s3_webhooks.py
git commit -m "feat: admin webhook CRUD (/webhooks; secret write-only)"
```

---

## Task 3: `webhooks.py` — payload, signature, delivery

**Files:**
- Create: `backend/app/webhooks.py`
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_phase3s3_webhooks.py` (append)

**Interfaces:**
- Produces: `build_payload(doc, approved_by) -> dict`; `sign(body: bytes, secret: str) -> str`; `deliver_webhook(config_id: int, document_id: int, approved_by: str) -> None`.

- [ ] **Step 1: Pin dep** — append `requests==2.34.2` to `backend/requirements.txt`; `pip install -r requirements.txt` (no-op, already present).

- [ ] **Step 2: Write the failing tests** — append to `backend/tests/test_phase3s3_webhooks.py`:
```python
import hmac
import json as _json
from hashlib import sha256
from app import webhooks
from app.models import Document, ExtractedField, AuditLog


def test_sign_matches_hmac():
    body = b'{"a":1}'
    assert webhooks.sign(body, "k") == "sha256=" + hmac.new(b"k", body, sha256).hexdigest()


def test_build_payload_shape(db_session):
    doc = Document(filename="a.pdf", document_type="invoice", stored_path="a",
                   status="approved", confidence=0.9)
    db_session.add(doc); db_session.flush()
    db_session.add(ExtractedField(document_id=doc.id, field_name="total", field_value="100",
                                  original_value="100", confidence=0.95, grounded="grounded"))
    db_session.commit(); db_session.refresh(doc)
    p = webhooks.build_payload(doc, "you@x.co")
    assert p["event"] == "document.approved" and p["approved_by"] == "you@x.co"
    assert p["fields"][0]["field_value"] == "100"


def _seed_config_and_doc(db, secret=None, active=True):
    from app.models import WebhookConfig
    cfg = WebhookConfig(document_type="invoice", url="https://hook/x", secret=secret, active=active)
    doc = Document(filename="a.pdf", document_type="invoice", stored_path="a", status="approved")
    db.add_all([cfg, doc]); db.commit(); db.refresh(cfg); db.refresh(doc)
    return cfg, doc


def test_deliver_posts_signed_and_audits_success(db_session, monkeypatch):
    cfg, doc = _seed_config_and_doc(db_session, secret="s3cr3t")
    captured = {}

    class FakeResp:
        status_code = 200

    def fake_post(url, data=None, headers=None, timeout=None):
        captured.update(url=url, data=data, headers=headers)
        return FakeResp()

    monkeypatch.setattr(webhooks, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(webhooks.requests, "post", fake_post)
    webhooks.deliver_webhook(cfg.id, doc.id, "you@x.co")
    assert captured["url"] == "https://hook/x"
    assert captured["headers"]["X-Aethermind-Signature"] == webhooks.sign(captured["data"], "s3cr3t")
    assert db_session.query(AuditLog).filter_by(document_id=doc.id, action="Webhook delivered").count() == 1


def test_deliver_no_secret_no_signature(db_session, monkeypatch):
    cfg, doc = _seed_config_and_doc(db_session, secret=None)
    captured = {}
    monkeypatch.setattr(webhooks, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(webhooks.requests, "post",
                        lambda url, data=None, headers=None, timeout=None: captured.update(headers=headers) or type("R", (), {"status_code": 200})())
    webhooks.deliver_webhook(cfg.id, doc.id, "you@x.co")
    assert "X-Aethermind-Signature" not in captured["headers"]


def test_deliver_failure_audits_and_never_raises(db_session, monkeypatch):
    cfg, doc = _seed_config_and_doc(db_session)
    monkeypatch.setattr(webhooks, "SessionLocal", lambda: db_session)
    def boom(*a, **k): raise RuntimeError("conn refused")
    monkeypatch.setattr(webhooks.requests, "post", boom)
    webhooks.deliver_webhook(cfg.id, doc.id, "you@x.co")   # must not raise
    assert db_session.query(AuditLog).filter_by(document_id=doc.id, action="Webhook failed").count() == 1


def test_deliver_inactive_config_no_post(db_session, monkeypatch):
    cfg, doc = _seed_config_and_doc(db_session, active=False)
    posted = {"v": False}
    monkeypatch.setattr(webhooks, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(webhooks.requests, "post",
                        lambda *a, **k: posted.__setitem__("v", True) or type("R", (), {"status_code": 200})())
    webhooks.deliver_webhook(cfg.id, doc.id, "you@x.co")
    assert posted["v"] is False
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_phase3s3_webhooks.py -k "sign or build_payload or deliver" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.webhooks'`

- [ ] **Step 4: Implement `backend/app/webhooks.py`:**
```python
import hmac
import json
import logging
from datetime import datetime, timezone
from hashlib import sha256
import requests
from .database import SessionLocal
from .models import Document, WebhookConfig
from .services import log

_log = logging.getLogger("aethermind")


def build_payload(doc: Document, approved_by: str) -> dict:
    return {
        "event": "document.approved", "document_id": doc.id, "filename": doc.filename,
        "document_type": doc.document_type, "status": doc.status, "confidence": doc.confidence,
        "approved_by": approved_by,
        "fields": [{"field_name": f.field_name, "field_value": f.field_value,
                    "confidence": f.confidence, "grounded": f.grounded}
                   for f in doc.extracted_fields],
        "anomalies": doc.anomalies, "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, sha256).hexdigest()


def deliver_webhook(config_id: int, document_id: int, approved_by: str) -> None:
    db = SessionLocal()
    try:
        cfg = db.get(WebhookConfig, config_id)
        doc = db.get(Document, document_id)
        if not cfg or not cfg.active or not doc:
            return
        body = json.dumps(build_payload(doc, approved_by)).encode()
        headers = {"Content-Type": "application/json"}
        if cfg.secret:
            headers["X-Aethermind-Signature"] = sign(body, cfg.secret)
        try:
            resp = requests.post(cfg.url, data=body, headers=headers, timeout=10)
            if 200 <= resp.status_code < 300:
                log(db, doc.id, "Webhook delivered", f"{cfg.url} ({resp.status_code})")
            else:
                log(db, doc.id, "Webhook failed", f"{cfg.url} -> HTTP {resp.status_code}")
        except Exception as exc:
            log(db, doc.id, "Webhook failed", f"{cfg.url} -> {type(exc).__name__}: {exc}")
        db.commit()
    except Exception:
        _log.exception("deliver_webhook error for config %s / doc %s", config_id, document_id)
    finally:
        db.close()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_phase3s3_webhooks.py -q`
Expected: PASS.

- [ ] **Step 6: Run full suite + commit**

Run: `pytest -q` → green.
```bash
git add backend/app/webhooks.py backend/requirements.txt backend/tests/test_phase3s3_webhooks.py
git commit -m "feat: webhook delivery — build_payload, HMAC sign, deliver_webhook (own session, requests)"
```

---

## Task 4: Push on approve

**Files:**
- Modify: `backend/app/main.py` (`update_document`)
- Test: `backend/tests/test_phase3s3_webhooks.py` (append)

**Interfaces:**
- Consumes: `webhooks.deliver_webhook`, `WebhookConfig`, `fastapi.BackgroundTasks`.

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/test_phase3s3_webhooks.py`:
```python
import app.main as main_mod
from app.models import Document as _Doc, WebhookConfig as _WC


def _approve(client, doc_id):
    return client.put(f"/document/{doc_id}", json={"fields": [], "action": "approve"})


def test_approve_with_active_config_schedules_delivery(client, db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(main_mod, "deliver_webhook",
                        lambda config_id, document_id, approved_by: calls.append((config_id, document_id)))
    doc = _Doc(filename="a.pdf", document_type="invoice", stored_path="a", status="review_required")
    cfg = _WC(document_type="invoice", url="https://h/x", active=True)
    db_session.add_all([doc, cfg]); db_session.commit(); db_session.refresh(doc); db_session.refresh(cfg)
    assert _approve(client, doc.id).status_code == 200
    assert calls == [(cfg.id, doc.id)]


def test_approve_without_config_no_delivery(client, db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(main_mod, "deliver_webhook", lambda *a, **k: calls.append(a))
    doc = _Doc(filename="a.pdf", document_type="invoice", stored_path="a", status="review_required")
    db_session.add(doc); db_session.commit(); db_session.refresh(doc)
    _approve(client, doc.id)
    assert calls == []


def test_save_action_no_delivery(client, db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(main_mod, "deliver_webhook", lambda *a, **k: calls.append(a))
    doc = _Doc(filename="a.pdf", document_type="invoice", stored_path="a", status="review_required")
    cfg = _WC(document_type="invoice", url="https://h/x", active=True)
    db_session.add_all([doc, cfg]); db_session.commit(); db_session.refresh(doc)
    client.put(f"/document/{doc.id}", json={"fields": [], "action": "save"})
    assert calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_phase3s3_webhooks.py -k "approve or save_action" -v`
Expected: FAIL — no delivery scheduled.

- [ ] **Step 3: Implement** — in `backend/app/main.py`, add `from .webhooks import deliver_webhook` at the top, add `background_tasks: BackgroundTasks` to `update_document`, and after `db.commit(); db.refresh(doc)` (before `return serialize(doc)`) insert the push:
```python
@app.put("/document/{document_id}")
def update_document(document_id: int, payload: DocumentUpdate, background_tasks: BackgroundTasks,
                    db: Session = Depends(get_db),
                    user: User = Depends(auth.require_role("admin", "reviewer"))):
    doc = db.get(Document, document_id)
    if not doc: raise HTTPException(404, "Document not found")
    for change in payload.fields:
        field = db.query(ExtractedField).filter_by(document_id=document_id, field_name=change.field_name).first()
        if field:
            field.edited_by_user = field.field_value != change.field_value
            field.field_value, field.validated = change.field_value, change.validated
    outcome = resolve_review_action(payload.action, payload.reason)
    if outcome["status"] is not None: doc.status = outcome["status"]
    if outcome["review_required"] is not None: doc.review_required = outcome["review_required"]
    log(db, doc.id, outcome["log_action"], outcome["log_details"], actor=user)
    db.commit(); db.refresh(doc)
    if outcome["status"] == "approved":
        cfg = db.query(WebhookConfig).filter_by(document_type=doc.document_type, active=True).first()
        if cfg:
            background_tasks.add_task(deliver_webhook, cfg.id, doc.id, user.email)
    return serialize(doc)
```
(`WebhookConfig` was imported in Task 2; `BackgroundTasks` is already imported for `/process`.)

- [ ] **Step 4: Run tests + full suite**

Run: `pytest tests/test_phase3s3_webhooks.py -q && pytest -q`
Expected: green (existing `PUT /document` tests still pass — no config → no push).

- [ ] **Step 5: Commit**
```bash
git add backend/app/main.py backend/tests/test_phase3s3_webhooks.py
git commit -m "feat: push approved documents to their configured webhook (background task)"
```

---

## Task 5: Frontend admin Webhooks screen

**Files:**
- Create: `frontend/app/webhooks/page.tsx`
- Modify: `frontend/app/page.tsx` (admin nav link)
- Test: `frontend/app/webhooks.test.tsx`

**Interfaces:**
- Consumes: `api` from `../lib/api`; `isAdmin` from `../lib/auth`.

- [ ] **Step 1: Implement `frontend/app/webhooks/page.tsx`** (mirror `frontend/app/users/page.tsx`):
```tsx
"use client";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";

type Hook = { id: number; document_type: string; url: string; active: boolean; has_secret: boolean };

export default function WebhooksPage() {
  const [hooks, setHooks] = useState<Hook[]>([]);
  const [form, setForm] = useState({ document_type: "", url: "", secret: "" });
  const load = () => api("/webhooks").then(setHooks).catch(() => (window.location.href = "/login"));
  useEffect(() => { load(); }, []);
  async function add(e: React.FormEvent) {
    e.preventDefault();
    await api("/webhooks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(form) });
    setForm({ document_type: "", url: "", secret: "" }); load();
  }
  async function toggle(h: Hook) {
    await api(`/webhooks/${h.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ active: !h.active }) });
    load();
  }
  async function remove(h: Hook) {
    await api(`/webhooks/${h.id}`, { method: "DELETE" }); load();
  }
  return (
    <div className="mx-auto mt-10 max-w-2xl p-6">
      <h1 className="mb-4 text-xl font-semibold">Webhooks</h1>
      <form onSubmit={add} className="mb-6 flex flex-wrap gap-2">
        <input aria-label="Document type" placeholder="document type" value={form.document_type}
               onChange={(e) => setForm({ ...form, document_type: e.target.value })} className="rounded border p-2" />
        <input aria-label="URL" placeholder="https://…" value={form.url}
               onChange={(e) => setForm({ ...form, url: e.target.value })} className="flex-1 rounded border p-2" />
        <input aria-label="Secret" placeholder="signing secret (optional)" value={form.secret}
               onChange={(e) => setForm({ ...form, secret: e.target.value })} className="rounded border p-2" />
        <button className="rounded bg-black px-3 text-white">Add</button>
      </form>
      <ul className="space-y-2">
        {hooks.map((h) => (
          <li key={h.id} className="flex items-center justify-between rounded border p-3 text-sm">
            <span>{h.document_type} → {h.url}{h.has_secret ? " 🔒" : ""}{h.active ? "" : " (inactive)"}</span>
            <span className="flex gap-2">
              <button onClick={() => toggle(h)} className="rounded border px-2 py-1">{h.active ? "Disable" : "Enable"}</button>
              <button onClick={() => remove(h)} className="rounded border px-2 py-1">Delete</button>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 2: Add an admin nav link** — in `frontend/app/page.tsx`, wherever the admin-only "Users" link is rendered (`isAdmin(role) && ...`), add a sibling link to `/webhooks` (e.g. `<a href="/webhooks">Webhooks</a>`), guarded by the same `isAdmin(role)` condition.

- [ ] **Step 3: Write the render test** — `frontend/app/webhooks.test.tsx`:
```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import WebhooksPage from "./webhooks/page";

vi.mock("../lib/api", () => ({
  api: vi.fn(async (path: string) => {
    if (path === "/webhooks") return [{ id: 1, document_type: "invoice", url: "https://h/x", active: true, has_secret: true }];
    return {};
  }),
}));

describe("WebhooksPage", () => {
  beforeEach(() => vi.clearAllMocks());
  it("lists configured webhooks", async () => {
    render(<WebhooksPage />);
    await waitFor(() => expect(screen.getByText(/invoice → https:\/\/h\/x/)).toBeTruthy());
  });
});
```

- [ ] **Step 4: Run tests + build**

Run (from `frontend/`): `npx vitest run && npm run build`
Expected: all green; build succeeds.

- [ ] **Step 5: Commit**
```bash
git add frontend/app/webhooks frontend/app/page.tsx frontend/app/webhooks.test.tsx
git commit -m "feat: admin Webhooks screen + nav link"
```

---

## Task 6: Docs

**Files:**
- Modify: `docs/deployment.md`, `docs/roadmap.md`

- [ ] **Step 1: `docs/deployment.md`** — add a "Phase 3 (S3) — webhook push" note: admins configure a webhook per document type (Webhooks screen or `/webhooks` API); on approve, Aethermind POSTs the result JSON to that URL in the background (single attempt, 10s timeout), HMAC-SHA256-signed via `X-Aethermind-Signature` when a secret is set; outcome is audit-logged; migration `0004` runs via `alembic upgrade head`; new dep `requests==2.34.2` (pinned — do not bump).

- [ ] **Step 2: `docs/roadmap.md`** — under Phase 3, mark **S3 (webhook push) shipped** (✅); note S2 (Drive/Gmail ingestion) and S4 (enrichment) remain.

- [ ] **Step 3: Commit**
```bash
git add docs/deployment.md docs/roadmap.md
git commit -m "docs: Phase 3 S3 webhook push — deployment + roadmap"
```

---

## Self-Review

**Spec coverage:**
- `WebhookConfig` + migration `0004` + schemas → Task 1. Admin CRUD (+ 409 dup, secret hidden via `has_secret`) → Task 2. `build_payload`/`sign`/`deliver_webhook` (own session, HMAC when secret, audit success/failure, swallow, `requests`) → Task 3. Push-on-approve BackgroundTask (approve-only; active-config lookup) → Task 4. Admin UI + nav link → Task 5. Docs → Task 6. Async/10s/single-attempt → Task 3. `WebhookOut` never returns secret → Tasks 1/2.

**Placeholder scan:** No TBD/TODO. Migration `0004` is autogenerated (deterministic) with an explicit verify checklist. Frontend render test mocks `../lib/api`; if jsdom rendering proves impractical, the implementer should note the blocker and assert the list-mapping at a smaller unit.

**Type consistency:** `WebhookConfig(document_type/url/secret/active)`, `WebhookOut{id,document_type,url,active,has_secret}`, `_webhook_out(w)`, `build_payload(doc, approved_by)`, `sign(body, secret)`, `deliver_webhook(config_id, document_id, approved_by)`, and the `main.deliver_webhook` / `webhooks.requests` / `webhooks.SessionLocal` monkeypatch seams are consistent across tasks. `requests==2.34.2` pinned at the installed version.
