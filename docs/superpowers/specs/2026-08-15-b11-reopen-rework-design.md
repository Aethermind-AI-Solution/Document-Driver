# B11 — Reopen / Rework Design

**Date:** 2026-08-15
**Status:** Approved design (pre-plan)
**Roadmap item:** Slice **A2** from the 2026-08-15 council review — the reopen/rework safety-net that must exist before auto-approve (B9). Informed by a four-perspective design council (see `docs/council/2026-08-15-next-phase-council-review.md` for the standing council process).

## Goal

Once a document is `approved` or `rejected` it currently dead-ends — there is no sanctioned way back into review, and `/process` will silently re-run on a finalized document and wipe its human corrections. B11 adds a **reopen** path (re-review with corrections preserved, no AI re-run), backed by an **explicit status state machine** that also fixes the silent-reprocess bug. This is the foundation auto-approve (B9) builds on: an unattended approval must be walkable-back.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Reopen semantics | **Re-review** — move a finalized doc back into review with all fields + human corrections **preserved**; no AI re-run |
| Reopen source states | Only `approved` / `rejected` |
| Reopened status | Distinct **`reopened`** status string, with `review_required=True` kept alongside (existing queue/boolean logic surfaces it for free) |
| State machine | Explicit `TRANSITIONS` map + `can_transition()` enforced at `PUT /document` and `/process` (fails closed) |
| `/process` guard | Reprocessable only from `{uploaded, error, review_required, processed}`; blocks `processing/approved/rejected/reopened` |
| RBAC | Reopen `rejected` → admin+reviewer; reopen `approved` → **admin-only** |
| Reason | **Required** when reopening an `approved` doc; optional for `rejected` |
| Webhook re-fire | Re-approval re-POSTs the corrected result, payload carries an incrementing **`revision`** so consumers dedupe/upsert |
| Frontend | Gate the Review panel on status (terminal → read-only + Reopen); reopen keeps the panel open on the now-editable doc; ConfirmDialog warning on reopen-of-approved; `reopened` badge + filter |

## Non-goals (this slice)

- AI re-run on reopen (reopen never touches `ExtractedField`); "reprocess after reopen" as a first-class op.
- Optimistic-locking version column (the transition guards + `/process` block close the main races this slice targets); bulk/multi-select reopen.
- A separate "Resend to ERP" action / full webhook idempotency store; export tracking (`exported_at`); structured/categorized reopen reasons.
- Multi-tenancy, encryption, or other enterprise-track items (separate slices).

---

## Data model — migration `0006`

Add to `Document`:
```python
revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
```
Incremented on every `* → approved` transition. Included in the webhook payload. Backfills existing rows to `0` via `server_default`. No column needed for `reopened` (it's a `Document.status` string value). Hand-written migration, `op.batch_alter_table` (SQLite-safe), reversible; chains from `0005_schema_status`.

## State machine (`services.py`)

```python
TRANSITIONS = {
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
Adding a future status that forgets to declare transitions fails closed (empty set → nothing allowed). Enforced at:
- **`PUT /document/{id}`** — before applying any `outcome["status"]`, if it changes status and `not can_transition(doc.status, target)` → **409**.
- **`POST /process/{id}`** — replace the current `status=="processing"` 409 check with a positive allow-list: reprocessable only when `doc.status in {"uploaded","error","review_required","processed"}`, else **409** ("Cannot reprocess a document in state '<status>'"). This blocks the existing bug where `/process` on an `approved`/`reopened` doc wipes corrections.

## Reopen action

`schemas.DocumentUpdate.action` gains `"reopen"` (Literal now `approve|reject|save|reopen`).

`resolve_review_action` gains a `reopen` branch (or a sibling helper) returning `{status: "reopened", review_required: True, log_action: "Reopened", log_details: f"from {prior_status}" + reason}`. The `PUT /document/{id}` handler:
- Captures `prior_status = doc.status` before mutating.
- For `action == "reopen"`:
  - **Validity:** `prior_status` must be `approved`/`rejected` (else 400) and `can_transition(prior_status, "reopened")` holds.
  - **RBAC:** if `prior_status == "approved"` and `user.role != "admin"` → **403** ("Only an admin can reopen an approved document").
  - **Reason:** if `prior_status == "approved"` and no `reason` → **422/400** ("A reason is required to reopen an approved document").
  - Set `status="reopened"`, `review_required=True`; **do not touch `ExtractedField`**; audit `"Reopened"` (prior status + reason, actor stamped).
  - No webhook fires on reopen.
- For `action == "approve"`: on the transition to `approved`, **`doc.revision += 1`** (before the webhook dispatch), then the existing webhook-on-approve logic runs (now the payload carries `revision`).

## Webhook (`webhooks.py`)

`build_payload` adds `"revision": doc.revision` to the payload. `deliver_webhook` unchanged otherwise. Re-approval after reopen re-fires exactly like a first approval, now distinguishable downstream by the higher `revision`.

## Frontend (`frontend/app/page.tsx`)

Gate the `Review` component on `document.status` (currently gated on role only — a latent bug that lets a finalized doc be silently re-approved):
- **Terminal** (`approved`/`rejected`): fields render **read-only** (reuse the existing non-editable `FieldBody` path); the action bar is replaced by a **"Reopen for review"** button. For `approved`, the button is shown only when `isAdmin(role)`, opens a **ConfirmDialog** (reused) with copy: *"This document was approved and may already be in downstream systems. Reopening moves it back to review — downstream systems won't be notified automatically."*, and requires the reason textarea; for `rejected`, admin+reviewer, lighter (no forced reason).
- **Reviewable** (`review_required`/`reopened`/`processed`): existing editable fields + Approve/Save-pending/Reject bar.
- Reopen calls `submit("reopen", onReopened)`; `onReopened` **keeps the doc selected** (refreshes it to `reopened`, editable) instead of clearing the panel.
- Add a `reopened` entry to the `badges` map (distinct color, e.g. violet) and to the queue's `STATUS_OPTIONS` filter ("↺ Reopened").

RBAC in the UI mirrors the backend (admin-only reopen for approved); the backend enforces regardless.

## Error handling

- Reopen from a non-terminal state → 400/409 (invalid transition).
- Reopen `approved` without reason → 422/400; by a non-admin → 403.
- `/process` on a blocked state → 409, no field mutation.
- Reopen never fires a webhook and never mutates fields — corrections are always preserved.
- Concurrency: the transition guards make a stale action from an unexpected state 409 rather than silently apply; a full optimistic-lock version column is deferred.

## Testing (offline)

**Backend:**
- `can_transition`: allowed/blocked pairs incl. `approved→reopened`, `reopened→approved`, `approved→approved` (blocked), `reopened` not reprocessable.
- `PUT /document` `action="reopen"`: from `approved` (admin, with reason) → `reopened` + `review_required=True` + `"Reopened"` audit (prior status recorded) + **fields untouched**; from `rejected` (reviewer) → reopened; from `review_required` → 400; approved-by-reviewer → 403; approved-without-reason → 422/400.
- `/process` blocked (409) on `approved`/`rejected`/`reopened`/`processing`; still works from `error`/`review_required`/`processed`/`uploaded`.
- `revision` increments on each approve; `build_payload` includes it; re-approval after reopen re-fires the webhook with the incremented revision (spy `deliver_webhook`/`requests.post`).
- Migration `0006` adds `revision` (default 0), reversible.

**Frontend (vitest, jsdom):**
- A terminal (`approved`) doc renders read-only fields + a Reopen button (admin) and NOT the Approve/Reject bar; a `rejected` doc reopenable by a reviewer.
- Clicking Reopen on an approved doc opens the ConfirmDialog; confirming sends `action:"reopen"`; the panel stays open (doc still selected).
- `reopened` badge renders; status filter includes it.
- Keep backend (175) / frontend (34) green, net new on top.

## Build sequencing (one plan, ordered tasks)

1. `Document.revision` + migration `0006` + `TRANSITIONS`/`can_transition` in `services.py`, with tests.
2. `/process` allow-list guard (block terminal/reopened) + tests.
3. `reopen` action end-to-end in `PUT /document` (validity, RBAC admin-only-for-approved, required-reason-for-approved, `reopened` status, audit, fields preserved) + `DocumentUpdate` schema, with tests.
4. `revision++` on approve + `revision` in `build_payload` + re-approval-refire test.
5. Frontend: status-gated Review panel + Reopen button + ConfirmDialog + `onReopened` keeps panel + `reopened` badge/filter, with tests.
6. Docs: `docs/roadmap.md` (mark A2 shipped; next A3), `docs/deployment.md` (migration 0006 note).

## Deployment notes

Migration `0006` runs via `alembic upgrade head`. No new dependencies, no new env vars. The webhook payload gains a `revision` field (additive; existing consumers ignore it). Feature is inert until a reviewer reopens a finalized document.
