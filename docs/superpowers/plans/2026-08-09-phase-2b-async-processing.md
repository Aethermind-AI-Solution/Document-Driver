# Phase 2b — Async Processing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `POST /process/{id}` dispatch the pipeline to a FastAPI BackgroundTask and return `202` immediately, with the frontend polling document status until done — so heavy runs no longer tie up a web request.

**Architecture:** A new `backend/app/jobs.py` holds the background worker (`run_pipeline_task`, opens its own DB session) and startup recovery (`reset_stuck_processing`). `/process` sets status `processing`, schedules the task, returns 202. The frontend polls `GET /document/{id}` until a terminal status, with a Retry on error. Reuses `document.status` — no schema, env, or dependency changes.

**Tech Stack:** FastAPI BackgroundTasks, SQLAlchemy, existing `run_pipeline` (Phase 2a), Next.js frontend.

## Global Constraints

- Python 3.13; backend venv at `backend/.venv` — `source backend/.venv/bin/activate`; run pytest from `backend/`.
- Tests run **offline** (no OpenAI/network). Async driven via `asyncio.run`; no `pytest-asyncio`.
- **TestClient runs BackgroundTasks synchronously after the response** — tests mock the task function to assert *dispatch* (202 + scheduled), not execution.
- Keep existing tests green: backend **99**, frontend **22**.
- No new columns, no new env vars, no new dependencies. Reuse `document.status` values: `uploaded | processing | processed | review_required | error`.
- `/process` keeps `Depends(rate_limit)` and `Depends(auth.require_role("admin","reviewer"))`; returns `202`; `409` if already `processing`; `404` if missing.
- Re-processing is idempotent (`run_pipeline` deletes+recreates `ExtractedField`s) — retry = re-call `/process`.
- Call collaborators via their module where tests need to monkeypatch (e.g. `main.run_pipeline_task`, `jobs.run_pipeline`, `jobs.SessionLocal`).

## File Structure

- `backend/app/jobs.py` — **new**: `run_pipeline_task(document_id, actor_id=None)`, `reset_stuck_processing(db) -> int`.
- `backend/app/main.py` — `/process` → 202 + BackgroundTasks + 409 guard; startup hook also calls `reset_stuck_processing`.
- `frontend/lib/api.ts` — **new** `pollDocument(id, opts)` (+ `TERMINAL`).
- `frontend/app/page.tsx` — `upload()` dispatch→poll flow, "Processing…" state, Retry on error.
- Tests: `backend/tests/test_phase2b_jobs.py`, `backend/tests/test_phase2b_process.py`, `frontend/lib/poll.test.ts`, a frontend render test for Retry.

---

## Task 1: Background worker + stuck-job recovery (`jobs.py`)

**Files:**
- Create: `backend/app/jobs.py`
- Test: `backend/tests/test_phase2b_jobs.py`

**Interfaces:**
- Produces:
  - `def run_pipeline_task(document_id: int, actor_id: int | None = None) -> None` — opens its own `SessionLocal`, loads the doc + optional actor, runs `run_pipeline` via `asyncio.run`, swallows exceptions (worker survives), always closes the session.
  - `def reset_stuck_processing(db: Session) -> int` — flips every `status=="processing"` Document → `"error"` with an audit "Processing failed" note; returns the count.

- [ ] **Step 1: Write the failing tests** — `backend/tests/test_phase2b_jobs.py`:

```python
import types
from app import jobs
from app.models import Document, AuditLog


def test_reset_stuck_processing_flips_only_processing(db_session):
    d1 = Document(filename="a.pdf", document_type="invoice", stored_path="a", status="processing")
    d2 = Document(filename="b.pdf", document_type="invoice", stored_path="b", status="processed")
    db_session.add_all([d1, d2]); db_session.commit()
    n = jobs.reset_stuck_processing(db_session)
    assert n == 1
    db_session.refresh(d1); db_session.refresh(d2)
    assert d1.status == "error" and d2.status == "processed"
    assert db_session.query(AuditLog).filter_by(document_id=d1.id, action="Processing failed").count() == 1


def test_run_pipeline_task_runs_and_closes(monkeypatch):
    closed = {"v": False}
    fake_doc = types.SimpleNamespace(id=1, document_type="invoice")

    class FakeSession:
        def get(self, model, ident): return fake_doc if ident == 1 else None
        def close(self): closed["v"] = True

    monkeypatch.setattr(jobs, "SessionLocal", lambda: FakeSession())
    seen = {}

    async def fake_run(db, doc, hint, actor=None):
        seen["doc"] = doc; seen["hint"] = hint; seen["actor"] = actor

    monkeypatch.setattr(jobs, "run_pipeline", fake_run)
    jobs.run_pipeline_task(1, None)
    assert seen["doc"] is fake_doc and seen["hint"] == "invoice" and seen["actor"] is None
    assert closed["v"] is True


def test_run_pipeline_task_swallows_errors(monkeypatch):
    closed = {"v": False}
    fake_doc = types.SimpleNamespace(id=1, document_type="invoice")

    class FakeSession:
        def get(self, model, ident): return fake_doc
        def close(self): closed["v"] = True

    monkeypatch.setattr(jobs, "SessionLocal", lambda: FakeSession())

    async def boom(db, doc, hint, actor=None): raise RuntimeError("kaboom")

    monkeypatch.setattr(jobs, "run_pipeline", boom)
    jobs.run_pipeline_task(1, None)   # must NOT raise
    assert closed["v"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_phase2b_jobs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.jobs'`

- [ ] **Step 3: Implement `backend/app/jobs.py`:**

```python
import asyncio
import logging
from sqlalchemy.orm import Session
from .database import SessionLocal
from .models import Document, User
from .services import log
from .agents.pipeline import run_pipeline

_log = logging.getLogger("aethermind")


def run_pipeline_task(document_id: int, actor_id: int | None = None) -> None:
    """Run the pipeline in a fresh DB session after the HTTP response is sent.
    run_pipeline sets status='error' + logs on failure; the guard here just keeps
    the background worker alive."""
    db = SessionLocal()
    try:
        doc = db.get(Document, document_id)
        if not doc:
            return
        actor = db.get(User, actor_id) if actor_id else None
        asyncio.run(run_pipeline(db, doc, doc.document_type, actor=actor))
    except Exception:
        _log.exception("Background pipeline failed for document %s", document_id)
    finally:
        db.close()


def reset_stuck_processing(db: Session) -> int:
    """Docs stranded in 'processing' (instance restarted mid-run) → 'error', retryable."""
    stuck = db.query(Document).filter(Document.status == "processing").all()
    for doc in stuck:
        doc.status = "error"
        log(db, doc.id, "Processing failed", "Processing interrupted (server restart)")
    db.commit()
    return len(stuck)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_phase2b_jobs.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run full suite**

Run: `pytest -q`
Expected: green (99 + 3 = 102).

- [ ] **Step 6: Commit**

```bash
git add backend/app/jobs.py backend/tests/test_phase2b_jobs.py
git commit -m "feat: background pipeline worker + stuck-job recovery (jobs.py)"
```

---

## Task 2: `/process` → 202 dispatch + startup recovery

**Files:**
- Modify: `backend/app/main.py` (`/process` endpoint; `_bootstrap` startup hook; imports)
- Test: `backend/tests/test_phase2b_process.py`

**Interfaces:**
- Consumes: `jobs.run_pipeline_task`, `jobs.reset_stuck_processing`, `fastapi.BackgroundTasks`.
- Produces: `POST /process/{id}` returns `202` with the serialized doc (status `processing`), schedules `run_pipeline_task(doc.id, user.id)`; `409` if already `processing`; `404` if missing.

- [ ] **Step 1: Write the failing tests** — `backend/tests/test_phase2b_process.py`:

```python
import app.main as main_mod
from app.models import Document


def test_process_returns_202_and_dispatches(client, db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(main_mod, "run_pipeline_task",
                        lambda document_id, actor_id: calls.append((document_id, actor_id)))
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x", status="uploaded")
    db_session.add(doc); db_session.commit(); db_session.refresh(doc)

    resp = client.post(f"/process/{doc.id}")
    assert resp.status_code == 202
    assert resp.json()["status"] == "processing"
    # TestClient runs the background task after the response → the spy recorded it
    assert len(calls) == 1 and calls[0][0] == doc.id and calls[0][1] is not None


def test_process_409_when_already_processing(client, db_session, monkeypatch):
    monkeypatch.setattr(main_mod, "run_pipeline_task", lambda document_id, actor_id: None)
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x", status="processing")
    db_session.add(doc); db_session.commit(); db_session.refresh(doc)
    resp = client.post(f"/process/{doc.id}")
    assert resp.status_code == 409


def test_process_404_when_missing(client):
    assert client.post("/process/99999").status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_phase2b_process.py -v`
Expected: FAIL — current `/process` returns 200 and runs `process_document` inline (no 202/409).

- [ ] **Step 3: Update imports** — in `backend/app/main.py`:
  - Add `BackgroundTasks` to the FastAPI import: `from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, BackgroundTasks`.
  - Add: `from .jobs import run_pipeline_task, reset_stuck_processing`.

- [ ] **Step 4: Replace the `/process` endpoint** — swap the current body:

```python
@app.post("/process/{document_id}", status_code=202, dependencies=[Depends(rate_limit)])
def process(document_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db),
            user: User = Depends(auth.require_role("admin", "reviewer"))):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    if doc.status == "processing":
        raise HTTPException(409, "Document is already processing")
    doc.status = "processing"; db.commit(); db.refresh(doc)
    background_tasks.add_task(run_pipeline_task, doc.id, user.id)
    return serialize(doc)
```

- [ ] **Step 5: Wire startup recovery** — in `_bootstrap`:

```python
@app.on_event("startup")
def _bootstrap():
    from .services import bootstrap_admin
    db = next(get_db())
    try:
        bootstrap_admin(db)
        reset_stuck_processing(db)
    finally:
        db.close()
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_phase2b_process.py -q && pytest -q`
Expected: new tests pass; full suite green.

- [ ] **Step 7: Commit**

```bash
git add backend/app/main.py backend/tests/test_phase2b_process.py
git commit -m "feat: /process dispatches pipeline to a background task (202) + startup stuck-job reset"
```

---

## Task 3: Frontend `pollDocument` helper

**Files:**
- Modify: `frontend/lib/api.ts` (add `TERMINAL` + `pollDocument`)
- Test: `frontend/lib/poll.test.ts`

**Interfaces:**
- Produces:
  - `export const TERMINAL = ["processed", "review_required", "error"]`
  - `export async function pollDocument(id: number, opts?: {intervalMs?: number; timeoutMs?: number; fetchDoc?: (id: number) => Promise<any>}): Promise<any>` — resolves with the doc once its status is terminal; throws `{status:0}` on timeout. `fetchDoc` defaults to `api('/document/'+id)` (injectable for tests).

- [ ] **Step 1: Write the failing test** — `frontend/lib/poll.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { pollDocument, TERMINAL } from "./api";

describe("pollDocument", () => {
  it("resolves when status becomes terminal", async () => {
    let n = 0;
    const fetchDoc = async () => ({ id: 1, status: n++ === 0 ? "processing" : "processed" });
    const doc = await pollDocument(1, { intervalMs: 1, fetchDoc });
    expect(doc.status).toBe("processed");
  });

  it("throws on timeout while still processing", async () => {
    const fetchDoc = async () => ({ id: 1, status: "processing" });
    await expect(pollDocument(1, { intervalMs: 1, timeoutMs: 5, fetchDoc })).rejects.toThrow();
  });

  it("TERMINAL lists the three end states", () => {
    expect(TERMINAL).toEqual(["processed", "review_required", "error"]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run lib/poll.test.ts`
Expected: FAIL — `pollDocument`/`TERMINAL` not exported.

- [ ] **Step 3: Implement** — append to `frontend/lib/api.ts`:

```typescript
export const TERMINAL = ["processed", "review_required", "error"];

export async function pollDocument(
  id: number,
  opts: { intervalMs?: number; timeoutMs?: number; fetchDoc?: (id: number) => Promise<any> } = {},
) {
  const interval = opts.intervalMs ?? 2000;
  const timeout = opts.timeoutMs ?? 180000;
  const fetchDoc = opts.fetchDoc ?? ((i: number) => api(`/document/${i}`));
  const start = Date.now();
  for (;;) {
    const doc = await fetchDoc(id);
    if (TERMINAL.includes(doc.status)) return doc;
    if (Date.now() - start > timeout) throw Object.assign(new Error("timeout"), { status: 0 });
    await new Promise((r) => setTimeout(r, interval));
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `frontend/`): `npx vitest run lib/poll.test.ts`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/api.ts frontend/lib/poll.test.ts
git commit -m "feat: pollDocument helper — poll document status until terminal"
```

---

## Task 4: Frontend dispatch → poll flow + Retry

**Files:**
- Modify: `frontend/app/page.tsx` (`upload()`; Retry control on error docs)
- Test: `frontend/app/retry.test.tsx`

**Interfaces:**
- Consumes: `pollDocument`, `TERMINAL` from `lib/api`; `traceToSteps` from `lib/agent-timeline`.

**Context:** `upload()` currently does `const complete = await api('/process/'+doc.id, {method:'POST'})` and then plays the timeline from `complete.pipeline_trace`. With async, `/process` now returns a `processing` doc (202) — the trace isn't ready yet, so we must poll.

- [ ] **Step 1: Rework `upload()`** — replace the `/process` call + timeline section so it dispatches then polls. The intent (adapt to the file's existing dense style, keeping the surrounding upload/agent-event code):

```typescript
    // dispatch (returns 202, status "processing")
    await api(`/process/${doc.id}`, { method: "POST" });
    // poll until the pipeline finishes
    const complete = await pollDocument(doc.id);
    if (complete.status === "error") throw new Error("Processing failed");
    await playTimeline(complete.pipeline_trace?.length
      ? traceToSteps(complete.pipeline_trace)
      : deriveAgentTimeline({ schemaName: complete.document_type, fields: complete.fields }));
    // ...existing: set active doc / refresh list as today
```
Import `pollDocument` (and `TERMINAL` if used) from `../lib/api`, and `traceToSteps` from `../lib/agent-timeline` (already imported for the trace panel). Keep the existing `catch` that flips in-flight agent events to `attention` — a poll timeout or `error` status lands there.

- [ ] **Step 2: Add a Retry control for errored documents** — in the "Recent documents" queue rows, when `d.status === "error"`, render a **Retry** button that re-dispatches and re-polls:

```tsx
{d.status === "error" && (
  <button
    onClick={(e) => { e.stopPropagation(); retry(d.id); }}
    className="rounded-lg border border-slate-200 px-2 py-1 text-xs font-semibold hover:bg-slate-50"
  >Retry</button>
)}
```
with a handler:
```typescript
async function retry(id: number) {
  try {
    await api(`/process/${id}`, { method: "POST" });
    const done = await pollDocument(id);
    await refresh();
    if (done.status !== "error") openDocument(id);
  } catch (e: any) {
    setMessage(e?.message || "Retry failed.");
  }
}
```
(`e.stopPropagation()` prevents the row's `openDocument` click from also firing.)

- [ ] **Step 3: Write a render test** — `frontend/app/retry.test.tsx` (mock `../lib/api`; assert an errored document shows a Retry control):

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import Home from "./page";

vi.mock("../lib/api", () => ({
  api: vi.fn(async (path: string) => {
    if (path === "/auth/me") return { role: "admin", email: "a@b.co" };
    if (path === "/schemas") return [];
    if (path === "/documents") return [{ id: 5, filename: "bad.pdf", document_type: "invoice",
      status: "error", upload_date: "2026-08-09", review_required: false }];
    return {};
  }),
  me: vi.fn(async () => ({ role: "admin", email: "a@b.co" })),
  pollDocument: vi.fn(), downloadFile: vi.fn(), TERMINAL: ["processed", "review_required", "error"],
}));

describe("retry control", () => {
  beforeEach(() => vi.clearAllMocks());
  it("shows Retry on an errored document", async () => {
    render(<Home />);
    await waitFor(() => expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument());
  });
});
```
(If rendering `Home` in jsdom proves impractical after a genuine attempt, note the blocker in the report and fall back to asserting the retry wiring at a smaller unit; the poll logic itself is already covered in Task 3.)

- [ ] **Step 4: Run tests + build**

Run (from `frontend/`): `npx vitest run && npm run build`
Expected: all green; build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/page.tsx frontend/app/retry.test.tsx
git commit -m "feat: async upload flow — dispatch, poll for status, retry on error"
```

---

## Task 5: Docs + roadmap

**Files:**
- Modify: `docs/deployment.md`, `docs/roadmap.md`

- [ ] **Step 1: `docs/deployment.md`** — add a short "Phase 2b" note: `/process` now returns `202` and the UI polls `GET /document/{id}`; no new infra/env; stranded `processing` docs are reset to `error` on startup; durable queue (QStash) + structured logging remain future work.

- [ ] **Step 2: `docs/roadmap.md`** — under Phase 2, mark **async processing (2b) shipped** (✅), and note that the multi-document splitter, durable queue (QStash), and structured logging remain.

- [ ] **Step 3: Commit**

```bash
git add docs/deployment.md docs/roadmap.md
git commit -m "docs: Phase 2b async processing — deployment note + roadmap update"
```

---

## Self-Review

**Spec coverage:**
- Async dispatch (202 + BackgroundTask, 409, 404) → Task 2. Background worker w/ own session + error swallow → Task 1. Stuck-job recovery on startup → Tasks 1 (fn) + 2 (wiring). Retry via re-call → Task 4 (+ idempotency relied on from Phase 2a). Frontend poll + Processing state + Retry → Tasks 3–4. Reuse `document.status`, no schema/env/deps → honored throughout. Docs → Task 5.

**Placeholder scan:** No TBD/TODO. Task 4's frontend edits are described against the file's existing dense style with the exact snippets to insert and a stated fallback for the jsdom render test — the behavior and code are fully specified.

**Type consistency:** `run_pipeline_task(document_id, actor_id=None)`, `reset_stuck_processing(db)->int`, `pollDocument(id, opts)` / `TERMINAL`, and the `/process` 202/409/404 contract are used consistently across tasks. `main.run_pipeline_task` / `jobs.run_pipeline` / `jobs.SessionLocal` are referenced via their modules so the tests' monkeypatches land.
