# P0 GUI Sweep — Queue Pagination · Audit Trail · Confirm Dialogs — Design

**Date:** 2026-08-15
**Status:** Approved design (pre-plan)
**Roadmap item:** Slice **A1** from the 2026-08-15 council review (`docs/council/2026-08-15-next-phase-council-review.md`) — the quick, high-leverage operator-facing fixes on top of existing backend capability.

## Goal

The document queue silently caps at 6 documents (`frontend/app/page.tsx` fetches `/documents` with no params, then `docs.slice(0,6)`), even though the backend already supports `q`/`status` filtering — an operator cannot find or triage past the 6 most recent docs. This slice makes the queue **searchable, filterable, and paginated**; surfaces the **audit trail** that the API already returns; and adds **confirmation dialogs** to the three destructive actions that currently fire instantly with no undo.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Pagination | **Server-side** — `limit`/`offset` + `total` on `GET /documents`; response shape becomes `{items, total}` |
| List payload | **Slim** `serialize_summary` (no `fields`/`audit`/`pipeline_trace` in the list) |
| Dashboard tiles | Backed by a new **`GET /documents/stats`** (global aggregates) so tiles stay accurate under pagination |
| Audit trail | Rendered in the **Review panel** from the already-returned `document.audit` |
| Confirmations | **Reusable in-app confirm dialog** (accessible modal), wired to Reject / Deactivate user / Delete webhook |

## Non-goals (this slice)

- Source-document viewer / split view, bulk actions, line-item editing (separate P0/P1 slices).
- Sorting controls, saved filters, infinite scroll (Prev/Next pagination only).
- Any backend schema/migration change (none needed).
- Auth/tenancy changes.

---

## Backend

### `GET /documents` — pagination
Current: `documents(q="", status="")` returns a bare list of full `serialize(d)`.

New signature: `documents(q: str = "", status: str = "", limit: int = 20, offset: int = 0)`.
- Build the filtered query (existing `q` ilike + `status` eq), ordered `upload_date desc`.
- `total = query.count()` (filtered count, before limit/offset).
- `items = [serialize_summary(d) for d in query.offset(offset).limit(limit).all()]`.
- Return `{"items": items, "total": total}`.
- Clamp: `limit` to `1..100` (default 20), `offset` to `>= 0`.

### `serialize_summary(d)` — slim list DTO
```python
def serialize_summary(d: Document):
    return {"id": d.id, "filename": d.filename, "document_type": d.document_type,
            "upload_date": d.upload_date, "status": d.status, "confidence": d.confidence,
            "review_required": d.review_required, "processing_time": d.processing_time,
            "anomalies": d.anomalies}
```
Drops `fields`, `audit`, `pipeline_trace` (the full doc is still available via `GET /document/{id}` / `serialize`).

### `GET /documents/stats` — dashboard aggregates
`stats(db, _: User = Depends(auth.get_current_user))` over **all** documents (no filter):
```python
{"total": <count>,
 "review_required": <count where review_required is True>,
 "avg_processing_time": <avg of non-null processing_time, or None>}
```
Used only by the dashboard tiles. Any authenticated user (same access level as `/documents`).

**Routing note:** declare `GET /documents/stats` so it isn't shadowed — `/documents` and `/documents/stats` are distinct literal paths (no `{id}` param on this route), so order is not a concern.

## Frontend

### Queue controls (`frontend/app/page.tsx`)
- New state: `q` (search text), `statusFilter` (""|status), `offset` (default 0), `total`. `PAGE_SIZE = 20`.
- `refresh()` calls `api(\`/documents?q=${encodeURIComponent(q)}&status=${statusFilter}&limit=${PAGE_SIZE}&offset=${offset}\`)`, sets `docs = res.items`, `total = res.total`. Remove `docs.slice(0,6)` — render all `docs`.
- Dashboard tiles read a new `stats` state fetched from `/documents/stats` (in `refresh()` or alongside): Documents = `stats.total`, Reviews = `stats.review_required`, Automation = `total ? round((1 - review_required/total)*100)%`, Avg time = `stats.avg_processing_time`. Fall back to `—` when `stats` is null.
- **Search box** above the queue: debounced ~300ms; on change resets `offset` to 0 and refetches.
- **Status filter** dropdown: options `All / uploaded / processed / review_required / approved / rejected / error`; resets `offset` to 0 on change.
- **Pagination controls** under the list: "Prev" (disabled when `offset===0`) / "Next" (disabled when `offset+PAGE_SIZE >= total`), and a label "`{offset+1}–{min(offset+PAGE_SIZE, total)} of {total}`". Prev/Next adjust `offset` by `±PAGE_SIZE` and refetch.
- Empty-results state: "No documents match your filters." (distinct from the existing first-run "No documents yet" when `total===0` and no filters).
- The upload flow's post-upload `refresh()` keeps working (returns to current page/filters).

### Audit trail (Review panel)
- In `Review`, add a collapsible **Activity** section (below the fields, above/below the action buttons) rendering `document.audit` entries: each shows `action`, `actor_email` (or "system" when null), a localized `timestamp`, and `details`. Most-recent-first. Collapsed by default with a toggle; renders nothing gracefully when `audit` is empty/absent.

### Reusable confirm dialog (`frontend/app/components/ConfirmDialog.tsx`)
- Accessible modal component: props `{open, title, message, confirmLabel="Confirm", cancelLabel="Cancel", destructive=false, onConfirm, onCancel}`.
- `role="dialog"`, `aria-modal="true"`, `aria-labelledby` the title; backdrop; **Esc** cancels; Confirm/Cancel buttons; destructive variant styles Confirm red. Renders `null` when `!open`.
- Wire into three call sites, each opening the dialog and only performing the action on confirm:
  - **Reject** (`page.tsx` `Review`): "Reject this document? This finalizes it as rejected." (Reopen isn't built yet — B11 — so this is currently irreversible; the confirm is the guard.)
  - **Deactivate user** (`frontend/app/users/page.tsx`): "Deactivate {email}? They will lose access."
  - **Delete webhook** (`frontend/app/webhooks/page.tsx`): "Delete the webhook for {document_type}?"

## Error handling
- `/documents` / `/documents/stats` failure → existing 401→`/login`, else the current "API unavailable" message; tiles show `—`.
- Offset beyond `total` (e.g., after a filter change) → backend returns empty `items`; the UI shows the empty-filter state and Next is disabled.
- Debounced search avoids a request per keystroke.
- Confirm dialog: Esc / Cancel / backdrop click closes without acting; Confirm runs the action then closes.

## Testing

**Backend (offline):**
- `GET /documents?limit=&offset=` returns `{items, total}`; `total` reflects the full filtered count while `items` respects `limit`/`offset`; `q` and `status` still filter; items use the slim shape (no `fields`/`audit`).
- `GET /documents/stats` returns `{total, review_required, avg_processing_time}` computed over all docs (seed a mix of statuses + processing_times).
- Existing tests asserting `/documents` returns a list are updated to the `{items, total}` shape.

**Frontend (vitest + @testing-library/react, jsdom):**
- Queue renders items from a mocked `/documents` `{items,total}`, shows the "X–Y of N" label, and Next/Prev fire refetches with updated `offset` (assert the `api` mock is called with the new offset).
- Search input (debounced) and status filter trigger a refetch with the right query params.
- The Review panel renders audit entries from a mocked document with an `audit` array.
- `ConfirmDialog`: the wrapped destructive action does NOT call the API until Confirm is clicked; Cancel/Esc closes without calling.
- Keep the frontend suite green (currently 28) and backend green (currently 172), net new on top.

## Build sequencing (one plan, ordered tasks)

1. Backend: `serialize_summary` + `GET /documents` pagination (`{items,total}`) + `GET /documents/stats`; update existing `/documents` tests; new pagination/stats tests.
2. Frontend: `ConfirmDialog` component + tests (isolated, reused by later tasks).
3. Frontend: queue search + status filter + pagination wired to the new API; tiles from `/documents/stats`; tests.
4. Frontend: audit-trail section in the Review panel; tests.
5. Frontend: wire `ConfirmDialog` into Reject / Deactivate user / Delete webhook; tests.
6. Docs: `docs/roadmap.md` (mark A1 shipped) — no `.env`/deployment changes (no new env, no migration).

## Deployment notes

No migration, no new dependencies, no new env vars. The `/documents` response-shape change is internal (only the frontend consumes it; updated in the same slice). Render/Vercel redeploy via the existing flow.
