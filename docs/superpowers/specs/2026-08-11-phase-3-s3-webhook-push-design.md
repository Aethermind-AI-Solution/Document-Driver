# Phase 3 (S3) — Webhook Push on Approve Design

**Date:** 2026-08-11
**Status:** Approved design (pre-plan)
**Roadmap item:** Phase 3, slice **S3** — push approved results to a per-type webhook. S2 (Drive/Gmail ingestion) and S4 (enrichment) remain separate slices.

## Goal

When an operator **approves** a document, POST its structured result to a webhook configured **per document type**, so downstream systems (Zapier/Make/n8n/custom/ERP) receive finalized data automatically. Delivery is async, optionally HMAC-signed, and never blocks the approve.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Trigger | **On approve only** (the `approve` action on `PUT /document/{id}`) |
| Config | **Per-document-type** `WebhookConfig` rows (DB), admin-managed |
| Delivery | **Async** via FastAPI `BackgroundTasks` (own DB session), single attempt, 10s timeout |
| Signing | **HMAC-SHA256** of the raw body → `X-Aethermind-Signature: sha256=<hex>` when a `secret` is set |
| HTTP | `requests` (already a transitive dep; pin it) |
| UI | Minimal admin **Webhooks** screen (list/add/toggle/delete) |
| Payload | Generic JSON (`event: "document.approved"` + the result) — no ERP-specific adapters |

## Non-goals (this slice)

- Retries / backoff / dead-letter (single attempt this slice).
- Push on process/reject; multiple webhooks per type; ERP-specific formats.
- Delivery-log persistence beyond the audit entry.

---

## Data model

**Migration `0004`** adds `WebhookConfig` (chains from `0003_pipeline_trace`):
```python
class WebhookConfig(Base):
    __tablename__ = "webhook_configs"
    id: int PK
    document_type: str unique, indexed
    url: str
    secret: str | None            # HMAC signing key; unsigned when null
    active: bool = True
    created_at: datetime = _utcnow
```
Keyed by `document_type` string (works for builtin `invoice`/`purchase_order` and custom schema keys alike). No FK to `SchemaDefinition` (builtins aren't rows).

## Admin endpoints (all `require_role("admin")`)

- `GET /webhooks` → list `WebhookOut`.
- `POST /webhooks` (201) → `WebhookCreate {document_type, url, secret?, active?}`; **409** if a config for that `document_type` already exists.
- `PATCH /webhooks/{id}` → `WebhookUpdate {url?, secret?, active?}`; 404 if missing.
- `DELETE /webhooks/{id}` (204) → 404 if missing.
- Schemas: `WebhookCreate`, `WebhookUpdate`, `WebhookOut {id, document_type, url, active, has_secret}` — **`WebhookOut` never returns the secret** (returns `has_secret: bool`).

## Push on approve

- `PUT /document/{document_id}` gains `background_tasks: BackgroundTasks`. After `resolve_review_action`, when the outcome status is `"approved"`, look up an **active** `WebhookConfig` for `doc.document_type`; if found, `background_tasks.add_task(deliver_webhook, config.id, doc.id, user.email)` (after the commit, so the approved state is durable before delivery).
- New module `backend/app/webhooks.py`:
  - `build_payload(doc, approved_by) -> dict` — `{event:"document.approved", document_id, filename, document_type, status, confidence, approved_by, fields:[{field_name,field_value,confidence,grounded}], anomalies, timestamp}` (timestamp = `datetime.now(timezone.utc).isoformat()`).
  - `sign(body: bytes, secret: str) -> str` — `"sha256=" + hmac.new(secret.encode(), body, sha256).hexdigest()`.
  - `deliver_webhook(config_id, document_id, approved_by)` — opens its own `SessionLocal`; loads the config + doc; if config missing/inactive → return; builds the JSON body (`json.dumps`), sets `Content-Type: application/json` + the signature header when `secret`; `requests.post(url, data=body, headers=..., timeout=10)`; writes an audit entry `"Webhook delivered"` (2xx) or `"Webhook failed: <status/reason>"` (non-2xx or exception); swallows exceptions so the worker survives; closes the session.

## Frontend

Admin **Webhooks** screen (`frontend/app/webhooks/page.tsx`, `me()`-guarded to admin): a table of configs (type, url, active) with add (type/url/secret), toggle active (PATCH), and delete. A "Webhooks" nav link shows only for admins (like the Users screen). Reuses the `api()` client.

## Error handling

- Webhook down / timeout / non-2xx → audit `"Webhook failed: …"`; approve already succeeded (delivery is best-effort, async).
- No config or inactive for the type → no push (silent, expected).
- Non-approve actions (save/reject) → no push.
- `deliver_webhook` never raises out of the background task.
- The URL is **admin-configured** (not end-user input) — a controlled outbound POST; the trust boundary is limited to admins (documented).

## Testing (offline — no real HTTP/network)

- **Model + migration `0004`:** upgrade head adds `webhook_configs`; chains from `0003`; reversible.
- **CRUD:** admin-only (non-admin → 403); create (201) + duplicate type → 409; list; patch (url/active); delete (204) + 404; `WebhookOut` omits `secret` and exposes `has_secret`.
- **`sign`:** known key+body → expected `sha256=<hex>` (deterministic).
- **`build_payload`:** shape + `event` + `approved_by` + fields/anomalies.
- **`deliver_webhook`:** `requests.post` monkeypatched to capture `url/data/headers` and return a fake 200 → posts the signed body (signature header present + correct when secret set, absent when no secret) and writes a `"Webhook delivered"` audit; a fake non-2xx / raised exception → `"Webhook failed"` audit and no raise; missing/inactive config → no post.
- **Push-on-approve wiring:** `PUT /document/{id}` with `action="approve"` and an active config → schedules `deliver_webhook` (spy; TestClient runs background tasks synchronously); no config → not scheduled; `action="save"`/`"reject"` → not scheduled.
- **Frontend:** admin Webhooks screen renders the list; a non-admin is redirected. Keep backend 134 / frontend 26 green (net new on top).

## Build sequencing (one plan, ordered tasks)

1. `WebhookConfig` model + migration `0004` + `WebhookCreate/Update/Out` schemas.
2. Admin CRUD endpoints (`GET/POST/PATCH/DELETE /webhooks`) + tests.
3. `webhooks.py` — `build_payload`, `sign`, `deliver_webhook` (own session, `requests` stubbed) + `requests` pin + tests.
4. Push-on-approve wiring in `PUT /document/{id}` (BackgroundTask) + tests.
5. Frontend admin Webhooks screen + nav link + tests.
6. Docs: `.env.example` (none needed — DB-config), `docs/deployment.md`, `docs/roadmap.md` (S3 shipped; S2/S4 remain).

## Deployment notes

New dep `requests` (pinned; already installed transitively). Migration `0004` runs via the existing `alembic upgrade head` on deploy. No new env vars (config is per-type in the DB). Feature is inert until an admin adds a webhook config and a document of that type is approved.
