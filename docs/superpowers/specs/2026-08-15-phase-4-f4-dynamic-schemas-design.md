# Phase 4 (F4 — Slice 1) — Dynamic Schemas (Schema-Author) Design

**Date:** 2026-08-15
**Status:** Approved design (pre-plan)
**Roadmap item:** Phase 4, **F4** first slice — AI-suggested / dynamic schemas so fields aren't hardcoded. Deeper confidence work (B4+) and any on-demand "suggest a schema from this doc" admin action remain separate later slices.

## Goal

Make Aethermind **adapt to any document type**. Today the classifier picks among existing schema keys; when nothing fits confidently (`conf < 0.5`) it **force-fits `invoice`**, so a genuinely novel document is extracted against the wrong fields. This slice adds a **Schema-Author agent**: when no approved schema fits, an LLM proposes a schema matching the document, the pipeline extracts *this* document against that fresh draft immediately, and the draft is persisted as `status="suggested"` for an admin to review (edit / approve / reject). Only **approved** schemas are offered to the classifier for future documents — a human gate on the reusable schema catalog, consistent with the app's human-in-the-loop philosophy.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Trigger | **Auto on low-confidence classify** — runs exactly where the pipeline would otherwise force-fit `invoice` (`conf < 0.5`, no usable hint) |
| Handling of proposal | **Draft + extract now** — extract the triggering doc against the proposed schema immediately (doc → `review_required`); persist the schema as a `"suggested"` draft |
| Reuse gate | Classifier offers only **approved** schemas (builtins + approved custom) to future docs; a draft becomes classifier-visible only after admin approval |
| Admin review actions | **Approve / reject / edit** (edit the proposed `name`/`fields` before approving) |
| Pipeline integration | **Called from the classifier** — isolated `schema_author.py` module invoked inside `ClassifierAgent`; no change to the pipeline's stage list |
| Toggle | `SCHEMA_AUTHOR_ENABLED` (default `True`) |

## Non-goals (this slice)

- On-demand "suggest a schema from this document" admin action (auto-only this slice).
- Automatic de-duplication of near-identical suggested drafts (admins reject dupes; auto-dedup is a fast-follow).
- Confidence-model changes (B4+); dynamic re-processing of already-processed docs; multi-document splitting.
- Changing how builtins are defined (they remain code in `document_schemas.py`, always approved).

---

## Data model

**Migration `0005`** (chains from `0004_webhook_configs`) adds three columns to `schema_definitions`:

```python
status: Mapped[str] = mapped_column(String(20), default="approved", server_default="approved")
origin_document_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
```

- `status` — `"approved"` (default) or `"suggested"`. The default + `server_default` keeps every existing row and every future manual `POST /schemas` **approved**; only the Schema-Author writes `"suggested"`.
- `origin_document_id` — the document that triggered the proposal (nullable; null for manually-created schemas). No FK constraint (keep migration simple and avoid delete-ordering coupling); it's a soft reference used for review display only.
- `created_at` — for ordering the review list most-recent-first.

Builtins in `document_schemas.py` have no DB row and are always treated as approved.

## Schema-Author module + classifier hook

New module `backend/app/agents/schema_author.py`:

- `async def propose_schema(text: str) -> dict | None`
  - LLM call (`config.CLASSIFIER_MODEL`, strict `json_schema` response format) returning `{key, name, fields}` where each field is `{name, label, type, required}`.
  - **Validation / normalization** (all applied before returning; any violation that can't be repaired → return `None`):
    - `key`: slugify to `^[a-z0-9_-]+$`; if it collides with an existing schema key (builtin or DB), append `-2`, `-3`, … until unique.
    - `fields`: each `name` normalized to `^[a-z0-9_]+$`; drop duplicates by `name`; `type` coerced to one of `string|number|date|array` (default `string` when unrecognized); `label` defaults to a title-cased `name` when missing; `required` coerced to bool (default `False`).
    - Reject (return `None`) when: fewer than 1 valid field, more than **25** fields, or `name`/`key` empty after normalization.
  - Wrapped in try/except → any exception returns `None` (never breaks the pipeline).
- `def persist_suggested(db, proposal: dict, origin_document_id: int) -> SchemaDefinition`
  - Creates a `SchemaDefinition(key, name, fields, status="suggested", origin_document_id=…)`, `db.add` + flush; audits `"Schema suggested"` with the key/name in details (actor is the pipeline actor). Returns the row.

`ClassifierAgent.run` change: after `classify_document`, when `config.SCHEMA_AUTHOR_ENABLED` **and** there is no confident match (the case that currently falls through to the `invoice` fallback — `key` not in approved keys or `conf < 0.5`, and `hint_type` isn't a real approved key):

```python
proposal = await propose_schema(text)
if proposal:
    row = persist_suggested(c.db, proposal, c.document.id)
    c.document.document_type = row.key
    c.schema = {"name": row.name, "fields": row.fields}
    c._detail = f"authored:{row.key}"
    # trace note "Schema authored: <key>"
```

On `propose_schema` returning `None` (disabled, LLM failure, or invalid output) the existing fallback path runs unchanged. The downstream extractor / reconciler / validator stages are untouched — they consume `c.schema` as before.

## Service-layer change: approved-only for the classifier

`services.available_schemas(db)` today returns builtins + **all** DB rows. It is consumed by the classifier (`classifier.py`), by `GET /schemas` (the upload type selector), by the `POST /schemas` duplicate-key check (`main.py`), and by the MCP `list_document_types` tool (`mcp_server.py`). Change it to return **approved only** (builtins + rows where `status == "approved"`), so suggested drafts never appear as a selectable / classifiable / externally-listed type. Manually-created schemas default to `"approved"`, so this is behavior-preserving for them. Hiding drafts from `list_document_types` is intentional and consistent (external agents never see unapproved drafts).

`services.schema_for(db, key)` is unchanged — it resolves a schema by key **regardless of status**, so the triggering document extracts and displays against its fresh draft, and an approved schema keeps working.

**Duplicate-key check must remain global.** `SchemaDefinition.key` is `unique=True`. The `POST /schemas` 409 dup-check must therefore compare against **all** schema keys — builtins + every DB row regardless of status — not the now-approved-only `available_schemas`. Otherwise an admin manually creating a schema whose key matches an existing *suggested* draft would pass the 409 check and then hit a DB unique-constraint error (500). Add a small helper `services.all_schema_keys(db) -> set[str]` (builtin keys ∪ every `SchemaDefinition.key`) and use it for the `POST /schemas` check. `propose_schema` also uniquifies proposed keys against this same global set, so authored drafts never collide either.

## Admin endpoints (all `require_role("admin")`)

- `GET /schemas/suggested` → list drafts (`status == "suggested"`), most-recent-first: `{id, key, name, fields, origin_document_id, created_at}`.
- `PATCH /schemas/{id}` → edit `name` and/or `fields` of a schema row; body reuses `SchemaFieldDef` validation (`fields: list[SchemaFieldDef]`, `min_length=1`); 404 if the id is missing; audits `"Schema edited"`.
- `POST /schemas/{id}/approve` → set `status="approved"`; 404 if missing; audits `"Schema approved"`. Idempotent (approving an approved row is a no-op success).
- `DELETE /schemas/{id}` (204) → reject/delete the row; 404 if missing; audits `"Schema rejected"`.

The existing `GET /schemas` (any authenticated user) and `POST /schemas` (admin create, auto-approved) are unchanged in contract, except that `POST /schemas`'s duplicate-key check now uses the global `all_schema_keys(db)` (see above) rather than `available_schemas`. The `{id}` path params are `int`-typed, so `GET /schemas/suggested` cannot be mis-routed to an `{id}` handler.

## Frontend

Admin **Suggested Schemas** screen (`frontend/app/schemas/suggested/page.tsx`, admin-only), mirroring the Webhooks/Users admin screens and reusing the `api()` client:

- Lists suggested drafts: name, key, origin document id, `created_at`, and the field list.
- A lightweight **field editor** per draft: add / remove a field, edit `label`, choose `type` (`string|number|date|array`), toggle `required`. Saving issues `PATCH /schemas/{id}`.
- **Approve** button → `POST /schemas/{id}/approve`; **Reject** button → `DELETE /schemas/{id}`. Both reload the list.
- An admin-only **"Suggested Schemas"** nav link in `frontend/app/page.tsx`, guarded by the same `role && isAdmin(role)` condition and matching className as the existing Users / Webhooks links.

## Config, cold-start, cost, safety

- Config addition: `SCHEMA_AUTHOR_ENABLED` (bool, default `True`).
- **Cold-path unchanged:** a confident classification (`conf >= 0.5`) never calls the author — no extra cost, identical behavior to today.
- **Cost:** one additional LLM call (classifier-tier model) only on low-confidence documents, bounded by the field cap.
- **Failure isolation:** `propose_schema` and `persist_suggested` are guarded; on any error the pipeline falls back to today's behavior and the document still processes.
- **Trust boundary:** the proposed schema is derived from document content, but it is *structure only* (field names/labels/types) and is never auto-approved — an admin gates every schema before it enters the reusable catalog. Field names are normalized to a safe charset.

## Error handling

- LLM error / invalid JSON / empty or oversized field set in `propose_schema` → `None` → existing fallback (force-fit `invoice`/hint), pipeline continues.
- `persist_suggested` DB error → caught in the classifier hook → fallback path; document still processes.
- `SCHEMA_AUTHOR_ENABLED=False` → author never called; behavior identical to pre-F4.
- Key collision at proposal time → uniquified with a numeric suffix (never raises).
- Admin edits an id that doesn't exist / approve / delete missing → 404.

## Testing (offline — no real LLM/network)

- **`propose_schema`:** with the LLM client stubbed to return a well-formed proposal → returns normalized `{key,name,fields}`; key collision with an existing key → uniquified suffix; field names normalized + deduped; unknown `type` → coerced to `string`; empty/oversized (>25) field set → `None`; stub raising → `None`.
- **`persist_suggested`:** creates a `status="suggested"` row with `origin_document_id` set and writes a `"Schema suggested"` audit.
- **Classifier hook:** `ClassifierAgent.run` on a low-confidence classify (stub `classify_document` → low conf) with `SCHEMA_AUTHOR_ENABLED=True` calls `propose_schema` (stubbed), persists a draft, and sets `document_type`/`schema` to the draft (trace detail reflects it); on a confident classify the author is **not** called; with `SCHEMA_AUTHOR_ENABLED=False` the author is never called and the fallback path runs.
- **`available_schemas` filter:** a `"suggested"` row is excluded from `available_schemas(db)` (so the classifier can't pick it) while `schema_for(db, key)` still resolves it; after approval it appears in `available_schemas`.
- **Global dup-check:** `POST /schemas` with a `key` equal to an existing **suggested** draft still returns 409 (via `all_schema_keys`), not a 500.
- **Admin endpoints:** `GET /schemas/suggested` lists only drafts; `PATCH /schemas/{id}` edits fields (validation rejects an empty field list); `POST /schemas/{id}/approve` flips status and makes the schema classifier-visible; `DELETE /schemas/{id}` removes it (204) and 404 on missing; non-admin → 403 on all four.
- **End-to-end draft → approve visibility:** a persisted draft is invisible to `available_schemas`; after `approve` it is visible.
- **Frontend:** the Suggested Schemas screen renders the draft list (mocked `api`); a non-admin path is handled like the other admin screens. Keep the frontend suite green.
- Keep backend **148** / frontend **27** green (net new on top).

## Build sequencing (one plan, ordered tasks)

1. `SchemaDefinition` columns + migration `0005` (`status`/`origin_document_id`/`created_at`) + any schema/serializer additions.
2. `services.available_schemas` approved-only filter + `services.all_schema_keys` helper + repoint `POST /schemas` dup-check to it (tests: `schema_for` still resolves drafts; a suggested-draft key still 409s on manual `POST /schemas`).
3. `schema_author.py` — `propose_schema` (validate/normalize/dedup, guarded) + `persist_suggested`, with tests (stubbed LLM).
4. `ClassifierAgent` hook + `SCHEMA_AUTHOR_ENABLED` config, with tests (trigger only on low-confidence; disabled path).
5. Admin endpoints (`GET /schemas/suggested`, `PATCH /schemas/{id}`, `POST /schemas/{id}/approve`, `DELETE /schemas/{id}`) + tests.
6. Frontend Suggested Schemas screen + field editor + nav link + tests.
7. Docs: `.env.example` (`SCHEMA_AUTHOR_ENABLED`), `docs/deployment.md`, `docs/roadmap.md` (mark F4-S1 shipped; note remaining F4 work).

## Deployment notes

New optional env `SCHEMA_AUTHOR_ENABLED` (defaulted `True`). Migration `0005` runs via the existing `alembic upgrade head` on deploy. No new dependencies. The feature is inert on documents that classify confidently; it only activates on genuinely novel document types, and nothing enters the reusable schema catalog without an admin approving it.
