# Reject / Keep-Pending Review Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Reject (terminal) and Save-&-keep-pending review actions with an optional reason, alongside the existing Approve, across backend and frontend.

**Architecture:** Backend maps a `action` enum ("approve" | "reject" | "save") to a status/flag/audit outcome via a pure `resolve_review_action` helper, called from `PUT /document/{id}`. Frontend `Review` component gains a reason textarea and three buttons. A new backend pytest harness (dev-only) unit-tests the helper and endpoint.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, pytest + httpx (new, dev-only); Next.js 15 / React 19 / TypeScript / Tailwind frontend.

## Global Constraints

- Product name is **Aethermind** — never reintroduce "Atlas".
- `rejected` is a new value of the existing `status` column — **no DB migration, no new columns**. Reason is carried in the audit log `details`.
- Reject and Approve are **terminal**; no reopen/un-reject.
- Reason is **optional** on Reject and Save; empty/absent is allowed and normalized to a default audit string.
- Approve keeps its existing behavior of force-setting `validated: true` on all fields. Reject and Save send each field's **current** `validated` value.
- New test dependencies (pytest, httpx) are **dev-only**, in `backend/requirements-dev.txt` — never in `requirements.txt`.
- Backend tests must use an **isolated temporary SQLite DB**; they must not touch `database/document_intelligence.db`.
- Exact transition table (status `None` = leave unchanged):

  | action | status | review_required | log_action | log_details |
  |--------|--------|-----------------|------------|-------------|
  | approve | "approved" | False | "Approved" | "Human review completed" |
  | reject | "rejected" | False | "Rejected" | reason, or "No reason given" |
  | save | None | None | "Edited" | reason, or "Review values updated" |

---

### Task 1: Backend pytest harness (dev-only)

The backend has no test runner. Add pytest + httpx, a `backend/tests/` package with an isolated-DB `conftest.py`, and a permanent health test that exercises the harness end-to-end.

**Files:**
- Create: `backend/requirements-dev.txt`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_health.py`

**Interfaces:**
- Consumes: `app.database.Base`, `app.database.get_db`, `app.main.app`.
- Produces (fixtures for Task 2):
  - `db_session` — a SQLAlchemy `Session` bound to a temp SQLite file, with `app.dependency_overrides[get_db]` pointing at it.
  - `client` — a `fastapi.testclient.TestClient(app)` using that session.

- [ ] **Step 1: Write the failing health test**

Create `backend/tests/__init__.py` (empty file), and `backend/tests/test_health.py`:

```python
def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run it to confirm there is no runner / no fixtures yet**

Run: `cd backend && source .venv/bin/activate && python -m pytest -q`
Expected: FAIL — pytest not installed (`No module named pytest`) OR, if pytest resolves, `fixture 'client' not found`.

- [ ] **Step 3: Add dev requirements and install**

Create `backend/requirements-dev.txt`:

```
pytest==8.3.4
httpx==0.28.1
```

Run: `cd backend && source .venv/bin/activate && pip install -r requirements-dev.txt`
Expected: installs pytest and httpx without error.

- [ ] **Step 4: Add the isolated-DB conftest**

Create `backend/tests/conftest.py`:

```python
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base, get_db
from app.main import app


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield session
    finally:
        app.dependency_overrides.clear()
        session.close()


@pytest.fixture
def client(db_session):
    return TestClient(app)
```

- [ ] **Step 5: Run the health test to verify the harness works**

Run: `cd backend && source .venv/bin/activate && python -m pytest -q`
Expected: PASS — 1 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/requirements-dev.txt backend/tests/__init__.py backend/tests/conftest.py backend/tests/test_health.py
git commit -m "test: add dev-only pytest harness with isolated DB to backend

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Backend reject/keep-pending logic (TDD)

Add the pure `resolve_review_action` helper, switch the request schema to an `action` enum, and rewire the endpoint. Unit-test the helper and the endpoint.

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/services.py` (add `resolve_review_action`)
- Modify: `backend/app/main.py` (endpoint `update_document`)
- Test: `backend/tests/test_review_actions.py` (unit)
- Test: `backend/tests/test_review_endpoint.py` (endpoint)

**Interfaces:**
- Consumes: `db_session`, `client` fixtures (Task 1); `app.models.Document`, `app.models.ExtractedField`.
- Produces:
  - `resolve_review_action(action: str, reason: str | None) -> dict` with keys `status` (`str | None`), `review_required` (`bool | None`), `log_action` (`str`), `log_details` (`str`).
  - `DocumentUpdate` with fields `fields: list[FieldUpdate]`, `action: Literal["approve","reject","save"] = "save"`, `reason: str | None = None`.

- [ ] **Step 1: Write the failing unit tests for the helper**

Create `backend/tests/test_review_actions.py`:

```python
from app.services import resolve_review_action


def test_approve():
    assert resolve_review_action("approve", None) == {
        "status": "approved",
        "review_required": False,
        "log_action": "Approved",
        "log_details": "Human review completed",
    }


def test_reject_with_reason():
    out = resolve_review_action("reject", "GST illegible")
    assert out["status"] == "rejected"
    assert out["review_required"] is False
    assert out["log_action"] == "Rejected"
    assert out["log_details"] == "GST illegible"


def test_reject_without_reason_uses_default():
    assert resolve_review_action("reject", None)["log_details"] == "No reason given"
    assert resolve_review_action("reject", "")["log_details"] == "No reason given"


def test_save_leaves_status_unchanged():
    out = resolve_review_action("save", None)
    assert out["status"] is None
    assert out["review_required"] is None
    assert out["log_action"] == "Edited"
    assert out["log_details"] == "Review values updated"


def test_save_with_reason():
    assert resolve_review_action("save", "fixed a typo")["log_details"] == "fixed a typo"


def test_unknown_action_defaults_to_save():
    out = resolve_review_action("bogus", None)
    assert out["status"] is None
    assert out["log_action"] == "Edited"
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_review_actions.py -q`
Expected: FAIL — `ImportError: cannot import name 'resolve_review_action'`.

- [ ] **Step 3: Implement the helper**

In `backend/app/services.py`, add this function (place it just below the `log` function near the top):

```python
def resolve_review_action(action: str, reason: str | None) -> dict:
    """Map a review action to status/flag overrides and an audit entry.

    status/review_required of None mean "leave the document's value unchanged".
    """
    if action == "approve":
        return {"status": "approved", "review_required": False,
                "log_action": "Approved", "log_details": "Human review completed"}
    if action == "reject":
        return {"status": "rejected", "review_required": False,
                "log_action": "Rejected", "log_details": reason or "No reason given"}
    return {"status": None, "review_required": None,
            "log_action": "Edited", "log_details": reason or "Review values updated"}
```

- [ ] **Step 4: Run the unit tests to verify they pass**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_review_actions.py -q`
Expected: PASS — 6 passed.

- [ ] **Step 5: Write the failing endpoint tests**

Create `backend/tests/test_review_endpoint.py`:

```python
from app.models import Document, ExtractedField


def _seed(session):
    doc = Document(filename="x.pdf", document_type="invoice", status="processed",
                   review_required=True, stored_path="/tmp/x.pdf")
    session.add(doc)
    session.flush()
    session.add(ExtractedField(document_id=doc.id, field_name="total",
                               field_value="100", confidence=0.7, validated=True))
    session.commit()
    return doc.id


def _put(client, doc_id, action, reason=None, value="100"):
    return client.put(f"/document/{doc_id}", json={
        "fields": [{"field_name": "total", "field_value": value, "validated": True}],
        "action": action, "reason": reason,
    })


def test_reject_sets_status_and_logs_reason(client, db_session):
    doc_id = _seed(db_session)
    resp = _put(client, doc_id, "reject", reason="blurry scan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert body["review_required"] is False
    rejected = [a for a in body["audit"] if a["action"] == "Rejected"]
    assert len(rejected) == 1 and rejected[0]["details"] == "blurry scan"


def test_reject_without_reason_uses_default(client, db_session):
    doc_id = _seed(db_session)
    body = _put(client, doc_id, "reject").json()
    rejected = [a for a in body["audit"] if a["action"] == "Rejected"]
    assert rejected[0]["details"] == "No reason given"


def test_save_keeps_status_pending(client, db_session):
    doc_id = _seed(db_session)
    body = _put(client, doc_id, "save", value="120").json()
    assert body["status"] == "processed"
    assert body["review_required"] is True
    assert any(a["action"] == "Edited" for a in body["audit"])
    assert [f for f in body["fields"] if f["field_name"] == "total"][0]["field_value"] == "120"


def test_approve_still_approves(client, db_session):
    doc_id = _seed(db_session)
    body = _put(client, doc_id, "approve").json()
    assert body["status"] == "approved"
    assert body["review_required"] is False
    assert any(a["action"] == "Approved" for a in body["audit"])
```

- [ ] **Step 6: Run to verify they fail**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_review_endpoint.py -q`
Expected: FAIL — the endpoint still uses `payload.approve`; sending `action` is ignored so status stays `processed` (reject/approve assertions fail), and Pydantic may reject unknown fields depending on config. This confirms the schema/endpoint still need changing.

- [ ] **Step 7: Update the request schema**

Replace the contents of `backend/app/schemas.py` with:

```python
from pydantic import BaseModel, Field
from typing import Any, Literal

class FieldUpdate(BaseModel):
    field_name: str
    field_value: str | None = None
    validated: bool = False

class DocumentUpdate(BaseModel):
    fields: list[FieldUpdate]
    action: Literal["approve", "reject", "save"] = "save"
    reason: str | None = None

class SchemaPayload(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_-]+$")
    name: str
    fields: list[dict[str, Any]]
```

- [ ] **Step 8: Rewire the endpoint**

In `backend/app/main.py`:

1. Add `resolve_review_action` to the services import (line 12):

```python
from .services import available_schemas, log, process_document, resolve_review_action, schema_for
```

2. In `update_document`, replace these two lines:

```python
    if payload.approve: doc.status, doc.review_required = "approved", False; log(db, doc.id, "Approved", "Human review completed")
    else: log(db, doc.id, "Edited", "Review values updated")
```

with:

```python
    outcome = resolve_review_action(payload.action, payload.reason)
    if outcome["status"] is not None: doc.status = outcome["status"]
    if outcome["review_required"] is not None: doc.review_required = outcome["review_required"]
    log(db, doc.id, outcome["log_action"], outcome["log_details"])
```

(Leave the field-edit loop above it and the `db.commit(); db.refresh(doc); return serialize(doc)` line below it unchanged.)

- [ ] **Step 9: Run the full backend suite to verify everything passes**

Run: `cd backend && source .venv/bin/activate && python -m pytest -q`
Expected: PASS — health + 6 unit + 4 endpoint = 11 passed.

- [ ] **Step 10: Commit**

```bash
git add backend/app/schemas.py backend/app/services.py backend/app/main.py backend/tests/test_review_actions.py backend/tests/test_review_endpoint.py
git commit -m "feat: add reject and keep-pending review actions to backend

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Frontend reject / save-pending UI

Add a reason textarea and Reject / Save-pending buttons to the `Review` component, a `rejected` badge, and Review Agent events for the new actions.

**Files:**
- Modify: `frontend/app/page.tsx`

**Interfaces:**
- Consumes the backend `action`/`reason` payload from Task 2.
- Produces: no exports; updated UI.

- [ ] **Step 1: Add the `rejected` badge style**

In `frontend/app/page.tsx`, find the `badges` constant and add a `rejected` entry:

```tsx
const badges:Record<string,string>={approved:"bg-emerald-50 text-emerald-700",rejected:"bg-rose-50 text-rose-700",review_required:"bg-amber-50 text-amber-700",processed:"bg-blue-50 text-blue-700",error:"bg-red-50 text-red-700",uploaded:"bg-slate-100 text-slate-600"};
```

- [ ] **Step 2: Wire the new callbacks at the `Review` call site**

In the `Home` component's `return`, find the `<Review .../>` element inside `<aside>` and replace it with (adds `onRejected` and `onSavedPending`; keeps `onSaved` and `onExport`):

```tsx
<Review document={selected} onSaved={()=>{const review=begin("Review Agent","Saving human approval");finish(review,"Document approved by human reviewer");refresh();setSelected(null)}} onRejected={()=>{const review=begin("Review Agent","Recording rejection");finish(review,"Document rejected","attention");refresh();setSelected(null)}} onSavedPending={()=>{const review=begin("Review Agent","Saving reviewer edits");finish(review,"Saved — pending approval");refresh()}} onExport={()=>{const exportEvent=begin("Export Agent","Generating ERP-ready CSV payload");finish(exportEvent,"ERP payload generated")}}/>
```

- [ ] **Step 3: Replace the `Review` component**

Replace the entire `function Review({document,onSaved,onExport}:{...}){...}` definition (the last function in the file) with:

```tsx
function Review({document,onSaved,onRejected,onSavedPending,onExport}:{document:any,onSaved:()=>void,onRejected:()=>void,onSavedPending:()=>void,onExport:()=>void}){const [saving,setSaving]=useState(false);const [reason,setReason]=useState(""); if(!document)return <section className="card h-fit p-6"><p className="label">Human review</p><h3 className="mt-1 text-lg font-semibold">Select a processed document</h3><p className="mt-2 text-sm leading-6 text-slate-600">Low-confidence values are highlighted for an operator to verify, correct, and approve.</p></section>; const submit=async(action:"approve"|"reject"|"save",done:()=>void)=>{setSaving(true);await api(`/document/${document.id}`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({action,reason:reason||null,fields:document.fields.map((f:any)=>({field_name:f.field_name,field_value:f.field_value,validated:action==="approve"?true:f.validated}))})});setSaving(false);setReason("");done()};const exportCsv=()=>{onExport();window.setTimeout(()=>window.location.assign(`${process.env.NEXT_PUBLIC_API_URL||"http://127.0.0.1:8000"}/export/${document.id}?format=csv`),150)};return <section className="card h-fit overflow-hidden"><div className="border-b border-slate-100 p-5"><p className="label">Human review · {document.document_type.replaceAll("_"," ")}</p><h3 className="mt-1 font-semibold">{document.filename}</h3><p className="mt-1 text-sm text-slate-500">Confidence {Math.round((document.confidence||0)*100)}%</p></div><div className="max-h-[420px] overflow-auto p-3">{document.fields.map((f:any)=><label key={f.id} className={`mb-2 block rounded-lg p-3 ${f.confidence<.8?"bg-red-50":f.confidence<.9?"bg-amber-50":"bg-slate-50"}`}><span className="flex justify-between text-xs font-semibold uppercase tracking-wide text-slate-500"><span>{f.field_name.replaceAll("_"," ")}</span><span>{Math.round(f.confidence*100)}%</span></span><input className="mt-2 w-full rounded border border-slate-200 bg-white px-2 py-1.5 text-sm" defaultValue={f.field_value||""} onChange={e=>f.field_value=e.target.value}/></label>)}</div><div className="space-y-3 border-t border-slate-100 p-4"><textarea value={reason} onChange={e=>setReason(e.target.value)} rows={2} placeholder="Optional reason (for reject or save)…" className="w-full rounded border border-slate-200 px-2 py-1.5 text-sm"/><div className="flex gap-2"><button className="btn-primary flex-1" onClick={()=>submit("approve",onSaved)} disabled={saving}>{saving?"Saving…":"Approve"}</button><button className="btn-secondary" onClick={()=>submit("save",onSavedPending)} disabled={saving}>Save pending</button><button className="rounded-lg bg-rose-600 px-3 py-2 text-sm font-semibold text-white hover:bg-rose-700 disabled:opacity-60" onClick={()=>submit("reject",onRejected)} disabled={saving}>Reject</button></div><button className="btn-secondary flex w-full items-center justify-center" onClick={exportCsv}>Export CSV <ArrowUpRight size={14} className="ml-1"/></button></div></section>}
```

Notes:
- Approve forces `validated: true` (unchanged behavior); Save/Reject send each field's current `validated`.
- `reason:reason||null` sends `null` when empty, matching the backend's `reason: str | None`.
- The fields list `max-h` was reduced from `520px` to `420px` to make room for the reason box + extra button.

- [ ] **Step 4: Verify types compile**

Run: `cd frontend && npx tsc --noEmit`
Expected: no type errors.

- [ ] **Step 5: Manually verify in the browser**

With both servers running (`uvicorn app.main:app --port 8000` and `npm run dev`), open `http://localhost:3000`, upload a document, open it in the review panel, and:
- Type a reason and click **Reject** → the doc's queue pill turns rose "rejected", the Review Agent event reads "Document rejected" (⚠), and the panel closes.
- Open another processed doc, edit a field, click **Save pending** → status stays (blue "processed"/amber "review required"), Review Agent reads "Saved — pending approval", panel stays open.
- Click **Approve** → status turns emerald "approved" as before.
- Click **Export CSV** → CSV downloads; Export Agent fires.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/page.tsx
git commit -m "feat: add reject and save-pending controls to review panel

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the implementer

- Match the existing dense one-line style of `page.tsx`; do not reformat.
- `btn-primary` / `btn-secondary` / `label` / `card` are existing Tailwind component classes in `frontend/app/globals.css` — reuse them.
- Backend tests always run as `cd backend && source .venv/bin/activate && python -m pytest` so the `app` package is importable from the backend directory.
