# Reject / Keep-Pending Review Flow — Design

**Date:** 2026-07-23
**Component:** Aethermind Document Intelligence Engine — backend + frontend
**Status:** Approved (design)

## Problem

The review panel offers only one terminal action: **Approve document**. There is
no way to reject a document or to save reviewer edits without approving. The
backend's `PUT /document/{id}` already supports a non-approve path (it logs
"Edited" when `approve` is false), but no UI control reaches it, and there is no
concept of rejection anywhere in the codebase.

## Goal

Give reviewers two alternatives to Approve:
- **Reject** — move the document to a terminal `rejected` state and drop it from
  the active review queue.
- **Save & keep pending** — persist reviewer edits without changing the
  document's status.

Both actions capture an **optional free-text reason** in the audit log.

## Non-goals (YAGNI)

- No reopen / un-reject / un-approve. `rejected` and `approved` are terminal.
- No authentication or actor identity (audit logs remain actor-less, as today).
- No re-processing / re-extraction of an existing document.
- No change to Approve's existing behavior of force-setting `validated: true` on
  every field (a separate known issue, explicitly out of scope).
- No reason field on Approve — reason applies to Reject and Save only.
- No new database columns or migrations.

## Decisions (from brainstorming)

- Reject is **terminal** (`status = "rejected"`).
- Reason is **optional** on both Reject and Save; empty is allowed.

## Backend design

### Request schema (`backend/app/schemas.py`)

Replace the `approve: bool` field with an explicit action enum. Only the project
frontend calls this endpoint, so this is a safe, clarifying change.

```python
from typing import Any, Literal

class DocumentUpdate(BaseModel):
    fields: list[FieldUpdate]
    action: Literal["approve", "reject", "save"] = "save"
    reason: str | None = None
```

`FieldUpdate` is unchanged.

### Pure helper (`backend/app/services.py`)

Add a pure function that maps an action to its side-effect data. This is the
unit-tested core.

```python
def resolve_review_action(action: str, reason: str | None) -> dict:
    """Map a review action to status/flag overrides and an audit entry.

    Returns keys:
      status: str | None            # None = leave document status unchanged
      review_required: bool | None  # None = leave unchanged
      log_action: str
      log_details: str
    """
```

Behavior:
| action | status | review_required | log_action | log_details |
|--------|--------|-----------------|------------|-------------|
| `approve` | `"approved"` | `False` | `"Approved"` | `"Human review completed"` |
| `reject` | `"rejected"` | `False` | `"Rejected"` | `reason` or `"No reason given"` |
| `save` | `None` | `None` | `"Edited"` | `reason` or `"Review values updated"` |

An unknown action is treated as `save` (defensive default; the Pydantic enum
already prevents it from reaching here in practice).

### Endpoint (`backend/app/main.py`)

`update_document` keeps applying field edits exactly as today, then replaces the
current `if payload.approve / else` block with:

```python
outcome = resolve_review_action(payload.action, payload.reason)
if outcome["status"] is not None:
    doc.status = outcome["status"]
if outcome["review_required"] is not None:
    doc.review_required = outcome["review_required"]
log(db, doc.id, outcome["log_action"], outcome["log_details"])
db.commit(); db.refresh(doc); return serialize(doc)
```

No change to `models.py` — `rejected` is a new value of the existing
`status: String(40)` column; `reason` is carried in the audit log's `details`.

## Frontend design (`frontend/app/page.tsx`)

Confined to the `Review` component plus the `badges` map and the `aside` wiring.

- Add an **optional reason `<textarea>`** above the action buttons.
- Buttons become: **Approve document** (existing `save()` → `action: "approve"`,
  keeps its force-`validated: true` behavior), **Reject** (rose styling →
  `action: "reject"`), **Save pending** (`action: "save"`). CSV export unchanged.
- Reject and Save send each field's **current** `validated` value (they do NOT
  force `true` — only Approve does).
- Add `rejected: "bg-rose-50 text-rose-700"` to the `badges` record so the queue
  shows a rejected pill.
- The `Review` component gains `onRejected` and `onSavedPending` callbacks
  (alongside the existing `onSaved`) that each fire a Review Agent activity
  event: "Document approved by human reviewer" / "Document rejected" /
  "Saved — pending approval". After Reject or Save, `refresh()` runs; after
  Reject the selection clears (like Approve); after Save the document stays open.

## Data flow

1. Reviewer edits fields, optionally types a reason, clicks one of the three
   buttons.
2. `PUT /document/{id}` with `{ fields, action, reason }`.
3. Backend applies edits → `resolve_review_action` → status/flag update + audit
   log → returns the serialized document.
4. Frontend fires the matching Review Agent event and refreshes the queue.

## Error handling

- The existing `try/catch` around the review save in `page.tsx` continues to
  surface failures via the banner `message`. No new failure modes are introduced;
  a failed `PUT` leaves the document in its prior state (no partial status
  change, since status is set server-side in one transaction).
- `reason` of any length is accepted (free text); no validation gate.

## Testing

### Backend (new test runner)

The backend has **no test runner** today. Add pytest + FastAPI `TestClient`
(via `httpx`) as **dev-only** dependencies in a new `backend/requirements-dev.txt`
(keeps `requirements.txt` production-only), plus a `backend/tests/` directory.

- **Unit** (`resolve_review_action`): one test per action asserting the exact
  status/flag/log tuple, plus the empty-reason and provided-reason variants for
  reject and save.
- **Endpoint** (`TestClient`, SQLite): upload → process → `PUT` with each action;
  assert the returned `status` (`approved` / `rejected` / unchanged) and that the
  audit log contains the expected action + reason. Use an isolated temporary
  SQLite database so tests do not touch `database/document_intelligence.db`.

### Frontend

Button→payload wiring and the reason textarea are verified in the browser
(UI glue; the decision logic lives and is tested in the backend). No new
frontend unit tests.

## Rollback

Revert the feature commits. The `status` column already tolerates any string, so
no schema rollback is needed; existing `approved`/`processed` documents are
unaffected.
