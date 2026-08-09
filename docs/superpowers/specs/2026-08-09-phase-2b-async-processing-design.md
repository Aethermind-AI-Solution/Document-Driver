# Phase 2b — Async Processing Design

**Date:** 2026-08-09
**Status:** Approved design (pre-plan)
**Roadmap item:** Phase 2b (partial) — async processing. Structured logging and a durable queue are explicitly deferred.

## Goal

Stop `/process` from running the multi-agent pipeline synchronously inside the HTTP request. Dispatch it to a FastAPI **BackgroundTask**, return `202` immediately, and let the frontend **poll** the document's status until the pipeline finishes — so heavy runs (classifier + per-page OpenAI calls) no longer tie up a web request or risk Render's proxy timeout.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Async mechanism | FastAPI `BackgroundTasks` (in-process; approach A) — $0, no new infra |
| Status delivery | Frontend polls `GET /document/{id}` |
| Job/status model | Reuse existing `document.status` (no new columns) |
| Stuck-job recovery | On startup, reset stranded `processing` docs → `error` (audit-noted, retryable) |
| Retry | Re-call `POST /process/{id}` (re-processing is idempotent — fields are deleted+recreated) |
| Upload UX | Auto-dispatch process after upload, then poll (same UX, non-blocking) |

## Non-goals (this cycle)

- Durable queue / retries via Upstash QStash (approach B) — documented upgrade for later.
- Structured logging backend (B15) — separate follow-up.
- SSE/WebSockets — polling is sufficient.
- Multi-document splitter, per-field-group fan-out (earlier deferrals, unchanged).

---

## Backend

### Dispatch: `POST /process/{document_id}`
Currently a sync endpoint that runs `process_document` (→ `asyncio.run(run_pipeline(...))`) inline and returns the finished doc. New behavior:
1. Load the doc; `404` if missing.
2. If `document.status == "processing"` → `409` ("Document is already processing") to prevent double-dispatch.
3. Set `document.status = "processing"`, commit (so the first poll immediately sees `processing`).
4. Schedule the pipeline: `background_tasks.add_task(run_pipeline_task, document.id, user.id)`.
5. Return `202` with the serialized doc (status `processing`).
6. Keep `Depends(rate_limit)` and `Depends(auth.require_role("admin", "reviewer"))`.

Signature gains `background_tasks: BackgroundTasks` and stays sync (`def`).

### Background worker function
New module-level function in `backend/app/main.py` (or `services.py`):
```python
def run_pipeline_task(document_id: int, actor_id: int | None) -> None:
    """Runs the pipeline in a fresh DB session after the response is sent."""
    db = SessionLocal()
    try:
        doc = db.get(Document, document_id)
        if not doc:
            return
        actor = db.get(User, actor_id) if actor_id else None
        asyncio.run(run_pipeline(db, doc, doc.document_type, actor=actor))
    except Exception:
        _log.exception("Background pipeline failed for document %s", document_id)
        # run_pipeline already set status="error" + logged; this guard just
        # keeps the background worker from crashing.
    finally:
        db.close()
```
- Uses its **own** `SessionLocal()` — the request's session is closed once the `202` is sent.
- `run_pipeline` already sets `status="error"`, logs "Processing failed", and re-raises on failure; the `except` here swallows that re-raise so the worker survives.

### Stuck-job recovery (startup)
`document.status == "processing"` with no running task = stranded (instance restarted mid-run). Add to the existing startup hook, alongside `bootstrap_admin`:
```python
def reset_stuck_processing(db) -> int:
    stuck = db.query(Document).filter(Document.status == "processing").all()
    for doc in stuck:
        doc.status = "error"
        log(db, doc.id, "Processing failed", "Processing interrupted (server restart)")
    db.commit()
    return len(stuck)
```
Runs once on boot. Stranded docs surface as `error` and are retryable.

### Retry
No new endpoint. A doc in `error` (or any non-`processing` state) can be re-dispatched by calling `POST /process/{id}` again. `run_pipeline` deletes the doc's `ExtractedField`s before recreating them, so re-processing is safe and non-duplicating (confirmed against current code).

### `process_document` shim
Keep the sync `process_document(db, document, actor=None)` shim (still used by tests and any direct caller). The endpoint no longer calls it; it calls `run_pipeline_task` via BackgroundTasks instead. The shim and `run_pipeline` are unchanged.

---

## Frontend

### Poll instead of block
Today `upload()` uploads then awaits `/process` and renders the finished result. New flow:
1. Upload → `POST /process/{id}` (returns 202, status `processing`).
2. **Poll** `GET /document/{id}` every **2s** until `status` ∈ {`processed`, `review_required`, `error`}, or a **~3 min** safety timeout.
3. While polling, show a "Processing…" state (the agent panel can show the in-progress/`processing` state; the real `pipeline_trace` renders once terminal).
4. On terminal status, render results/trace/anomalies as today.

Add a small helper in `frontend/lib/api.ts`:
```typescript
export async function pollDocument(id: number, opts?: {intervalMs?: number; timeoutMs?: number}) {
  const interval = opts?.intervalMs ?? 2000, timeout = opts?.timeoutMs ?? 180000;
  const start = Date.now();
  while (true) {
    const doc = await api(`/document/${id}`);
    if (["processed", "review_required", "error"].includes(doc.status)) return doc;
    if (Date.now() - start > timeout) throw Object.assign(new Error("timeout"), { status: 0 });
    await new Promise((r) => setTimeout(r, interval));
  }
}
```

### Retry
When a document's `status === "error"`, show a **Retry** button that re-calls `POST /process/{id}` and resumes polling. (Also covers the poll-timeout case.)

---

## Error handling

- Background-task failure → `run_pipeline` sets `status="error"` + audit; the worker guard prevents a crash.
- `/process` on an already-`processing` doc → `409`.
- Frontend poll timeout → surface a timeout message + Retry (does not leave the UI spinning forever).
- Startup resets stranded `processing` jobs → `error`.
- All error responses already carry CORS headers (Phase-2b hardening shipped) so the browser sees real statuses.

## Testing (offline)

- **Dispatch:** `/process` returns `202` with status `processing` and schedules the task — the task function is monkeypatched to a spy so the real pipeline (LLM) doesn't run; assert the spy was scheduled/called with the doc id and the response status is `processing`.
- **Double-dispatch:** `/process` on a `processing` doc → `409`.
- **Background task:** `run_pipeline_task` with a stubbed `run_pipeline` opens/uses/closes a session and swallows a raised error (worker survives); on success the doc reaches a terminal status.
- **Stuck recovery:** `reset_stuck_processing` flips `processing` docs → `error` with an audit entry; leaves terminal docs untouched.
- **Retry idempotency:** running the pipeline twice on a doc replaces its fields (no duplicates) — confirms re-dispatch safety.
- **Frontend:** `pollDocument` resolves when a mocked `api` returns `processing` then `processed`; throws on timeout; retry path re-dispatches on `error`.
- Keep backend 99 / frontend 22 green (net new tests on top).

Note on TestClient: Starlette's `TestClient` runs background tasks synchronously after the response, so tests mock the task function to assert *dispatch* (202 + scheduled) without executing the real pipeline.

## Build sequencing (one plan, ordered tasks)

1. `run_pipeline_task` + `reset_stuck_processing` (services/main) with unit tests.
2. Rework `/process` → 202 + BackgroundTask + 409 guard; wire startup reset (+ tests).
3. Frontend `pollDocument` helper (+ tests).
4. Frontend `upload()`/process flow → dispatch + poll + "Processing…" state + Retry button (+ tests).
5. Docs + roadmap update (mark 2b async shipped; note QStash + structured logging remain).

## Deployment notes

No new infrastructure, no new env vars. Behavior change only: `/process` now returns `202` and the UI polls. Render redeploys via the existing flow.
