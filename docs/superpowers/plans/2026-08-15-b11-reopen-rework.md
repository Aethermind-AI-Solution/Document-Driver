# B11 — Reopen / Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reopen path (re-review, corrections preserved) for finalized documents, backed by an explicit status state machine that also fixes the silent reprocess-wipes-corrections bug; re-approval re-fires the webhook with a `revision` for downstream dedup.

**Architecture:** A `TRANSITIONS` map + `can_transition()` in `services.py` gates every status change at `PUT /document` and `/process`. `PUT /document` gains a `reopen` action (approved/rejected → `reopened`, `review_required=True`, fields untouched, audited); reopening an `approved` doc is admin-only and requires a reason. `Document.revision` (migration `0006`) increments on approve and rides in the webhook payload. The frontend Review panel becomes status-aware (terminal → read-only + Reopen).

**Tech Stack:** FastAPI + SQLAlchemy + Alembic (backend), Next.js 15 / React 19 / TypeScript / Tailwind (frontend), pytest, vitest + @testing-library/react (jsdom).

**Spec:** `docs/superpowers/specs/2026-08-15-b11-reopen-rework-design.md`

## Global Constraints

- Reopen = re-review with `ExtractedField` rows **untouched** (no AI re-run, no field mutation); valid only from `approved`/`rejected`.
- Reopened docs use `status="reopened"` **with `review_required=True`**.
- `TRANSITIONS`/`can_transition` enforced at `PUT /document` (status changes) and `/process` (reprocessable allow-list `{uploaded, error, review_required, processed}`). Fails closed.
- Reopening `approved` → **admin-only** + **reason required** (403 / 422 otherwise); reopening `rejected` → admin+reviewer, reason optional.
- Non-`reopen` actions (approve/reject/save) on a terminal (`approved`/`rejected`) doc → **409** ("reopen first"). Reopen is the only door back in.
- `Document.revision` (int, default 0, `server_default="0"`) increments on each `→ approved`; included in `build_payload` as `revision`.
- Migration `0006` chains from `0005_schema_status`, hand-written `op.batch_alter_table`, reversible.
- No new dependencies, no new env vars. Keep backend **175** / frontend **34** tests green (net new on top).
- Match existing code style (dense `page.tsx`; targeted edits).

---

### Task 1: `Document.revision` + migration `0006` + state machine

**Files:**
- Modify: `backend/app/models.py` (Document)
- Create: `backend/alembic/versions/0006_document_revision.py`
- Modify: `backend/app/services.py` (add `TRANSITIONS` + `can_transition`)
- Test: `backend/tests/test_b11_reopen.py` (new)

**Interfaces:**
- Produces: `Document.revision: int` (default 0); `services.TRANSITIONS: dict[str, set[str]]`; `services.can_transition(current: str, target: str) -> bool`.

- [ ] **Step 1: Add the column.** In `backend/app/models.py`, in `class Document`, add after the `anomalies` line (before the relationships):

```python
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
```
(`Integer` is already imported.)

- [ ] **Step 2: Write the migration.** Create `backend/alembic/versions/0006_document_revision.py`:

```python
"""document revision counter

Revision ID: 0006_document_revision
Revises: 0005_schema_status
Create Date: 2026-08-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_document_revision"
down_revision: Union[str, None] = "0005_schema_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.drop_column("revision")
```

- [ ] **Step 3: Add the state machine.** In `backend/app/services.py`, add near the top (after the imports / before `log`):

```python
TRANSITIONS: dict[str, set[str]] = {
    "uploaded":        {"processing"},
    "processing":      {"processed", "review_required", "error"},
    "error":           {"processing"},
    "processed":       {"approved", "rejected"},
    "review_required": {"approved", "rejected"},
    "approved":        {"reopened"},
    "rejected":        {"reopened"},
    "reopened":        {"approved", "rejected"},
}


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, set())
```

- [ ] **Step 4: Write the failing tests.** Create `backend/tests/test_b11_reopen.py`:

```python
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from app import services

BACKEND = Path(__file__).resolve().parents[1]


def test_migration_0006_adds_revision(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    cols = {c["name"] for c in inspect(create_engine(url)).get_columns("documents")}
    assert "revision" in cols


def test_can_transition_allows_reopen_cycle():
    assert services.can_transition("approved", "reopened")
    assert services.can_transition("rejected", "reopened")
    assert services.can_transition("reopened", "approved")
    assert services.can_transition("reopened", "rejected")
    assert services.can_transition("review_required", "approved")


def test_can_transition_blocks_invalid():
    assert not services.can_transition("approved", "approved")
    assert not services.can_transition("approved", "rejected")
    assert not services.can_transition("rejected", "approved")
    assert not services.can_transition("processing", "approved")
    assert not services.can_transition("nonsense", "approved")
```

- [ ] **Step 5: Run the tests.** Run (from `backend/`): `python3 -m pytest tests/test_b11_reopen.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full suite.** Run (from `backend/`): `python3 -m pytest -q`
Expected: all green (178 = 175 + 3 new).

- [ ] **Step 7: Commit.**

```bash
git add backend/app/models.py backend/alembic/versions/0006_document_revision.py backend/app/services.py backend/tests/test_b11_reopen.py
git commit -m "feat: Document.revision + migration 0006 + status TRANSITIONS/can_transition"
```

---

### Task 2: `/process` reprocessable allow-list guard

**Files:**
- Modify: `backend/app/main.py` (`process` handler, ~line 212)
- Test: `backend/tests/test_b11_reopen.py` (append)

**Interfaces:**
- Consumes: nothing new (uses the status strings).

- [ ] **Step 1: Write the failing tests.** Append to `backend/tests/test_b11_reopen.py`:

```python
import pytest
from app.models import Document
import app.main as main_mod


def _doc(db, status):
    d = Document(filename="a.pdf", document_type="invoice", stored_path="p", status=status,
                 review_required=(status == "review_required"))
    db.add(d); db.commit(); db.refresh(d)
    return d


@pytest.mark.parametrize("status", ["approved", "rejected", "reopened", "processing"])
def test_process_blocked_on_non_reprocessable(client, db_session, monkeypatch, status):
    calls = []
    monkeypatch.setattr(main_mod, "run_pipeline_task", lambda *a, **k: calls.append(a))
    d = _doc(db_session, status)
    r = client.post(f"/process/{d.id}")
    assert r.status_code == 409
    assert calls == []                       # pipeline never dispatched
    db_session.refresh(d)
    assert d.status == status                 # status untouched


@pytest.mark.parametrize("status", ["uploaded", "error", "review_required", "processed"])
def test_process_allowed_on_reprocessable(client, db_session, monkeypatch, status):
    calls = []
    monkeypatch.setattr(main_mod, "run_pipeline_task", lambda *a, **k: calls.append(a))
    d = _doc(db_session, status)
    r = client.post(f"/process/{d.id}")
    assert r.status_code == 202
    db_session.refresh(d)
    assert d.status == "processing"
```

- [ ] **Step 2: Run the tests to verify they fail.** Run (from `backend/`): `python3 -m pytest tests/test_b11_reopen.py -k process -q`
Expected: FAIL — `approved`/`rejected`/`reopened` currently reprocess (202) instead of 409.

- [ ] **Step 3: Implement.** In `backend/app/main.py`, in the `process` handler, replace:

```python
    if doc.status == "processing":
        raise HTTPException(409, "Document is already processing")
    doc.status = "processing"; db.commit(); db.refresh(doc)
```

with:

```python
    if doc.status not in {"uploaded", "error", "review_required", "processed"}:
        raise HTTPException(409, f"Cannot reprocess a document in state '{doc.status}'")
    doc.status = "processing"; db.commit(); db.refresh(doc)
```

- [ ] **Step 4: Run the tests.** Run (from `backend/`): `python3 -m pytest tests/test_b11_reopen.py -k process -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite.** Run (from `backend/`): `python3 -m pytest -q`
Expected: all green (186 = 178 + 8 new parametrized cases). Existing upload/retry tests (process from `uploaded`/`error`) still pass.

- [ ] **Step 6: Commit.**

```bash
git add backend/app/main.py backend/tests/test_b11_reopen.py
git commit -m "feat: block /process on terminal/reopened/processing states (allow-list guard)"
```

---

### Task 3: `reopen` action in `PUT /document` + state-machine enforcement

**Files:**
- Modify: `backend/app/schemas.py` (`DocumentUpdate.action`)
- Modify: `backend/app/main.py` (`update_document` + import `can_transition`)
- Test: `backend/tests/test_b11_reopen.py` (append)

**Interfaces:**
- Consumes: `services.can_transition` (Task 1).
- Produces: `PUT /document/{id}` with `action="reopen"`.

- [ ] **Step 1: Extend the schema.** In `backend/app/schemas.py`, change `DocumentUpdate.action`:

```python
    action: Literal["approve", "reject", "save", "reopen"] = "save"
```

- [ ] **Step 2: Write the failing tests.** Append to `backend/tests/test_b11_reopen.py`:

```python
from app.models import ExtractedField, AuditLog, User
from app import auth
from app.database import get_db


def _reviewer(db):
    u = User(email="rev@t.local", password_hash="x", role="reviewer", is_active=True)
    db.add(u); db.commit(); db.refresh(u); return u


def _as(user):
    main_mod.app.dependency_overrides[auth.get_current_user] = lambda: user


def test_reopen_approved_by_admin_with_reason(client, db_session):
    d = _doc(db_session, "approved")
    db_session.add(ExtractedField(document_id=d.id, field_name="total", field_value="100",
                                  original_value="100", confidence=0.9)); db_session.commit()
    r = client.put(f"/document/{d.id}", json={"fields": [], "action": "reopen", "reason": "wrong total"})
    assert r.status_code == 200
    db_session.refresh(d)
    assert d.status == "reopened" and d.review_required is True
    # fields preserved
    assert db_session.query(ExtractedField).filter_by(document_id=d.id).count() == 1
    assert db_session.query(AuditLog).filter_by(document_id=d.id, action="Reopened").count() == 1


def test_reopen_rejected_by_reviewer(client, db_session):
    rev = _reviewer(db_session); _as(rev)
    try:
        d = _doc(db_session, "rejected")
        r = client.put(f"/document/{d.id}", json={"fields": [], "action": "reopen"})
        assert r.status_code == 200
        db_session.refresh(d); assert d.status == "reopened"
    finally:
        main_mod.app.dependency_overrides.pop(auth.get_current_user, None)


def test_reopen_approved_by_reviewer_forbidden(client, db_session):
    rev = _reviewer(db_session); _as(rev)
    try:
        d = _doc(db_session, "approved")
        r = client.put(f"/document/{d.id}", json={"fields": [], "action": "reopen", "reason": "x"})
        assert r.status_code == 403
    finally:
        main_mod.app.dependency_overrides.pop(auth.get_current_user, None)


def test_reopen_approved_without_reason_422(client, db_session):
    d = _doc(db_session, "approved")
    r = client.put(f"/document/{d.id}", json={"fields": [], "action": "reopen"})
    assert r.status_code == 422


def test_reopen_non_terminal_conflict(client, db_session):
    d = _doc(db_session, "review_required")
    r = client.put(f"/document/{d.id}", json={"fields": [], "action": "reopen"})
    assert r.status_code == 409


def test_approve_on_approved_conflicts(client, db_session):
    d = _doc(db_session, "approved")
    r = client.put(f"/document/{d.id}", json={"fields": [], "action": "approve"})
    assert r.status_code == 409           # must reopen first
```

(The `client` fixture authenticates as admin by default; the reviewer tests override `get_current_user`.)

- [ ] **Step 3: Run the tests to verify they fail.** Run (from `backend/`): `python3 -m pytest tests/test_b11_reopen.py -k reopen -q`
Expected: FAIL — `reopen` isn't handled; approve-on-approved currently succeeds.

- [ ] **Step 4: Implement.** In `backend/app/main.py`, update the services import (line 15) to include `can_transition`:

```python
from .services import all_schema_keys, available_schemas, can_transition, log, resolve_review_action, schema_for
```

Replace the body of `update_document` (from `doc = db.get(...)` through `return serialize(doc)`) with:

```python
    doc = db.get(Document, document_id)
    if not doc: raise HTTPException(404, "Document not found")
    prior_status = doc.status
    # Reopen: finalized -> back into review, fields untouched.
    if payload.action == "reopen":
        if prior_status not in ("approved", "rejected"):
            raise HTTPException(409, f"Cannot reopen a document in state '{prior_status}'")
        if prior_status == "approved":
            if user.role != "admin":
                raise HTTPException(403, "Only an admin can reopen an approved document")
            if not (payload.reason or "").strip():
                raise HTTPException(422, "A reason is required to reopen an approved document")
        doc.status = "reopened"; doc.review_required = True
        reason = (payload.reason or "").strip()
        log(db, doc.id, "Reopened", f"from {prior_status}" + (f": {reason}" if reason else ""), actor=user)
        db.commit(); db.refresh(doc)
        return serialize(doc)
    # Non-reopen actions are not allowed on a finalized document — reopen first.
    if prior_status in ("approved", "rejected"):
        raise HTTPException(409, f"Reopen the document before editing it (state '{prior_status}')")
    for change in payload.fields:
        field = db.query(ExtractedField).filter_by(document_id=document_id, field_name=change.field_name).first()
        if field:
            field.edited_by_user = field.field_value != change.field_value
            field.field_value, field.validated = change.field_value, change.validated
    outcome = resolve_review_action(payload.action, payload.reason)
    if outcome["status"] is not None:
        if not can_transition(prior_status, outcome["status"]):
            raise HTTPException(409, f"Cannot move a document from '{prior_status}' to '{outcome['status']}'")
        doc.status = outcome["status"]
    if outcome["review_required"] is not None: doc.review_required = outcome["review_required"]
    log(db, doc.id, outcome["log_action"], outcome["log_details"], actor=user)
    db.commit(); db.refresh(doc)
    if outcome["status"] == "approved":
        cfg = db.query(WebhookConfig).filter_by(document_type=doc.document_type, active=True).first()
        if cfg:
            background_tasks.add_task(deliver_webhook, cfg.id, doc.id, user.email)
    return serialize(doc)
```

(The `revision` increment on approve is added in Task 4.)

- [ ] **Step 5: Run the tests.** Run (from `backend/`): `python3 -m pytest tests/test_b11_reopen.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full suite.** Run (from `backend/`): `python3 -m pytest -q`
Expected: all green (192 = 186 + 6 new). Existing approve/reject tests (from `review_required`) still pass — those transitions remain valid.

- [ ] **Step 7: Commit.**

```bash
git add backend/app/schemas.py backend/app/main.py backend/tests/test_b11_reopen.py
git commit -m "feat: reopen action (admin-only+reason for approved) + transition guard in PUT /document"
```

---

### Task 4: `revision` increment on approve + in webhook payload

**Files:**
- Modify: `backend/app/main.py` (`update_document` approve path)
- Modify: `backend/app/webhooks.py` (`build_payload`)
- Test: `backend/tests/test_b11_reopen.py` (append)

**Interfaces:**
- Consumes: `Document.revision` (Task 1).

- [ ] **Step 1: Write the failing tests.** Append to `backend/tests/test_b11_reopen.py`:

```python
from app import webhooks


def test_revision_increments_on_approve(client, db_session):
    d = _doc(db_session, "review_required")
    client.put(f"/document/{d.id}", json={"fields": [], "action": "approve"})
    db_session.refresh(d)
    assert d.revision == 1


def test_build_payload_includes_revision(db_session):
    d = _doc(db_session, "approved")
    d.revision = 3; db_session.commit(); db_session.refresh(d)
    payload = webhooks.build_payload(d, "you@x.co")
    assert payload["revision"] == 3


def test_reapproval_after_reopen_refires_with_revision(client, db_session, monkeypatch):
    from app.models import WebhookConfig
    scheduled = []
    monkeypatch.setattr(main_mod, "deliver_webhook", lambda *a, **k: scheduled.append(a))
    db_session.add(WebhookConfig(document_type="invoice", url="https://h/x", active=True))
    d = _doc(db_session, "review_required"); db_session.commit()
    client.put(f"/document/{d.id}", json={"fields": [], "action": "approve"})   # revision 1, fires
    client.put(f"/document/{d.id}", json={"fields": [], "action": "reopen", "reason": "fix"})
    client.put(f"/document/{d.id}", json={"fields": [], "action": "approve"})   # revision 2, fires again
    db_session.refresh(d)
    assert d.revision == 2 and len(scheduled) == 2
```

- [ ] **Step 2: Run the tests to verify they fail.** Run (from `backend/`): `python3 -m pytest tests/test_b11_reopen.py -k "revision or reapproval" -q`
Expected: FAIL — `revision` never increments; `build_payload` has no `revision`.

- [ ] **Step 3: Implement.** In `backend/app/main.py` `update_document`, in the non-reopen branch, add the increment right after setting an approved status. Change:

```python
    if outcome["review_required"] is not None: doc.review_required = outcome["review_required"]
    log(db, doc.id, outcome["log_action"], outcome["log_details"], actor=user)
```

to:

```python
    if outcome["review_required"] is not None: doc.review_required = outcome["review_required"]
    if outcome["status"] == "approved": doc.revision += 1
    log(db, doc.id, outcome["log_action"], outcome["log_details"], actor=user)
```

In `backend/app/webhooks.py` `build_payload`, add `revision` to the returned dict (e.g. next to `confidence`):

```python
        "document_type": doc.document_type, "status": doc.status, "confidence": doc.confidence,
        "revision": doc.revision,
```

- [ ] **Step 4: Run the tests.** Run (from `backend/`): `python3 -m pytest tests/test_b11_reopen.py -k "revision or reapproval" -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite.** Run (from `backend/`): `python3 -m pytest -q`
Expected: all green (195 = 192 + 3 new). Existing webhook `build_payload` tests still pass (additive field).

- [ ] **Step 6: Commit.**

```bash
git add backend/app/main.py backend/app/webhooks.py backend/tests/test_b11_reopen.py
git commit -m "feat: increment Document.revision on approve; include revision in webhook payload"
```

---

### Task 5: Frontend — status-gated Review panel + Reopen

**Files:**
- Modify: `frontend/app/page.tsx` (`Review` component + `Home` badges/STATUS_OPTIONS/onReopened)
- Test: `frontend/app/reopen.test.tsx` (new)

**Interfaces:**
- Consumes: `ConfirmDialog` (already imported in `page.tsx`); `PUT /document` `action:"reopen"` (Task 3).

- [ ] **Step 1: Write the failing test.** Create `frontend/app/reopen.test.tsx`:

```tsx
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import Home from "./page";

const approvedDoc = {
  id: 3, filename: "inv.pdf", document_type: "invoice", status: "approved",
  confidence: 0.95, upload_date: "2026-08-15", review_required: false, anomalies: [],
  fields: [{ id: 1, field_name: "total", field_value: "100", confidence: 0.95, validated: true, grounded: "grounded" }],
  audit: [],
};
const apiMock = vi.fn(async (path: string, init?: any) => {
  if (path === "/auth/me") return { role: "admin", email: "a@b.co" };
  if (path === "/schemas") return [];
  if (path.startsWith("/documents/stats")) return { total: 1, review_required: 0, avg_processing_time: 1 };
  if (path.startsWith("/documents")) return { items: [approvedDoc], total: 1 };
  if (path.startsWith("/document/")) return approvedDoc;
  return {};
});
vi.mock("../lib/api", () => ({
  api: (p: string, i?: any) => apiMock(p, i),
  me: vi.fn(async () => ({ role: "admin", email: "a@b.co" })),
  pollDocument: vi.fn(), downloadFile: vi.fn(), TERMINAL: ["processed", "review_required", "error"],
}));

describe("reopen flow", () => {
  beforeEach(() => vi.clearAllMocks());
  it("shows Reopen (not Approve) on an approved doc and sends action:reopen after confirm", async () => {
    render(<Home />);
    await waitFor(() => expect(screen.getByText("inv.pdf")).toBeTruthy());
    fireEvent.click(screen.getByText("inv.pdf"));
    await waitFor(() => expect(screen.getByRole("button", { name: /reopen for review/i })).toBeTruthy());
    expect(screen.queryByRole("button", { name: /^approve$/i })).toBeNull();
    fireEvent.click(screen.getByRole ? screen.getByRole("button", { name: /reopen for review/i }) : screen.getByText(/reopen for review/i));
    // confirm dialog
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /^reopen$/i }));
    await waitFor(() =>
      expect(apiMock.mock.calls.some(c => c[1]?.body && String(c[1].body).includes('"action":"reopen"'))).toBe(true));
  });
});
```

- [ ] **Step 2: Run it to verify it fails.** Run (from `frontend/`): `npx vitest run reopen`
Expected: FAIL — the approved doc currently shows the Approve bar / editable fields, no Reopen button.

- [ ] **Step 3: Implement the badges + filter.** In `frontend/app/page.tsx`:
- In the `badges` map (line ~9) add a `reopened` entry:
```tsx
reopened:"bg-violet-50 text-violet-700",
```
- In `STATUS_OPTIONS` (added in the P0 sweep) add `"reopened"`:
```tsx
 const STATUS_OPTIONS=["","uploaded","processed","review_required","reopened","approved","rejected","error"];
```

- [ ] **Step 4: Wire `onReopened` in `Home`.** Where `<Review .../>` is rendered, add the `onReopened` prop (keeps the doc selected, refreshed):
```tsx
onReopened={()=>{const id=selected?.id;refresh();if(id)openDocument(id);}}
```

- [ ] **Step 5: Make the Review panel status-aware.** In the `Review` component:
- Update the signature to accept `onReopened`: add `onReopened:()=>void` to its props destructuring and type.
- Add near the other `Review` state:
```tsx
const [confirmReopen,setConfirmReopen]=useState(false);
const isTerminal=document.status==="approved"||document.status==="rejected";
```
- Change the `editable` computation to also require non-terminal:
```tsx
const editable=!!role&&canReview(role)&&!isTerminal;
```
- Replace the existing action block `{editable&&<div className="space-y-3 border-t border-slate-100 p-4">…</div>}` — keep that block exactly as-is for the non-terminal case (it is already gated on `editable`, which is now false for terminal docs), and ADD, immediately after it, the terminal action block:
```tsx
{!!role&&canReview(role)&&isTerminal&&<div className="space-y-3 border-t border-slate-100 p-4">
  {error&&<p className="text-sm font-medium text-rose-600">{error}</p>}
  {document.status==="approved"
    ? isAdmin(role)&&<>
        <textarea value={reason} onChange={e=>setReason(e.target.value)} rows={2} placeholder="Reason for reopening (required)…" className="w-full rounded border border-slate-200 px-2 py-1.5 text-sm"/>
        <button className="btn-secondary w-full" disabled={saving} onClick={()=>setConfirmReopen(true)}>Reopen for review</button>
      </>
    : <button className="btn-secondary w-full" disabled={saving} onClick={()=>submit("reopen",onReopened)}>Reopen for review</button>}
  <button className="btn-secondary flex w-full items-center justify-center" onClick={exportCsv}>Export CSV <ArrowUpRight size={14} className="ml-1"/></button>
  <ConfirmDialog open={confirmReopen} title="Reopen this document?" message="This document was approved and may already be in downstream systems. Reopening moves it back to review — downstream systems won't be notified automatically." confirmLabel="Reopen" onCancel={()=>setConfirmReopen(false)} onConfirm={()=>{setConfirmReopen(false);submit("reopen",onReopened);}}/>
</div>}
```
(`isAdmin` is already imported in `page.tsx`. `submit`/`reason`/`saving`/`error`/`exportCsv` already exist in `Review`.)

- [ ] **Step 6: Run tests + build.** Run (from `frontend/`): `npx vitest run && npm run build`
Expected: all green (36 = 34 + reopen(1) ... adjust to actual); build succeeds. If the `getByRole ?` ternary in the test reads awkwardly, simplify to `fireEvent.click(screen.getByRole("button",{name:/reopen for review/i}))` — keep the assertions.

- [ ] **Step 7: Commit.**

```bash
git add frontend/app/page.tsx frontend/app/reopen.test.tsx
git commit -m "feat: status-gated Review panel + Reopen (admin+confirm for approved) + reopened badge"
```

---

### Task 6: Docs

**Files:**
- Modify: `docs/roadmap.md`, `docs/deployment.md`

- [ ] **Step 1: `docs/roadmap.md`.** In the "Revised execution order (2026-08-15 council review)" note, mark **A2 (B11 reopen/rework) ✅ Shipped (2026-08-15)** and set the next item to **A3 (confidence calibration + eval harness)**. Keep the rest of the note intact.

- [ ] **Step 2: `docs/deployment.md`.** Add a `## B11 — reopen/rework` note under the most recent section: reviewers can reopen a finalized document back into review (fields/corrections preserved) via the Review panel; reopening an approved doc is admin-only and requires a reason; `/process` now refuses terminal/reopened docs; migration `0006` (adds `Document.revision`) runs via `alembic upgrade head`; the webhook payload gains an additive `revision` field. No new deps/env.

- [ ] **Step 3: Commit.**

```bash
git add docs/roadmap.md docs/deployment.md
git commit -m "docs: B11 reopen/rework — roadmap A2 shipped + deployment note"
```

---

## Self-Review

**Spec coverage:** `Document.revision` + migration `0006` + `TRANSITIONS`/`can_transition` → Task 1. `/process` allow-list guard → Task 2. Reopen action (validity, admin-only+reason for approved, `reopened` status, audit, fields preserved, non-reopen-on-terminal 409, transition guard on status changes) → Task 3. `revision++` on approve + payload `revision` + re-fire test → Task 4. Status-gated Review panel + Reopen + ConfirmDialog + `onReopened` + `reopened` badge/filter → Task 5. Docs → Task 6.

**Placeholder scan:** No TBD/TODO. Every code step has complete code. Task 5 Step 1's test uses a defensive `getByRole ?` ternary; Step 6 explicitly says to simplify it to `getByRole("button",{name:/reopen for review/i})` if awkward — not a placeholder, an instruction.

**Type consistency:** `can_transition(current, target) -> bool` defined in Task 1, imported + used in Tasks 2/3. `DocumentUpdate.action` gains `"reopen"` (Task 3) matching the frontend `submit("reopen",…)` (Task 5). `Document.revision` (Task 1) incremented in Task 4 and read in `build_payload` (Task 4). `reopened` status is produced by Task 3 and rendered/filtered by Task 5. Test counts are cumulative (175 → ~195 backend; 34 → ~36 frontend).
