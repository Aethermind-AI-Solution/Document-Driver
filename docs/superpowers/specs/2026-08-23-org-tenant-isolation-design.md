# Org Tenant Isolation (isolation-first) Design

**Date:** 2026-08-23
**Status:** Approved design (pre-plan)
**Roadmap item:** Enterprise track — **`org_id` tenant isolation**, the gap a prior council called the single most dangerous / disqualifying-for-multi-customer issue. Informed by a four-perspective design council (Architect broke the mechanism tie; Migration/data-ops vetted the backfill).

## Goal

Today every tenant-data query is unscoped: `GET /documents` / `GET /document/{id}` and ~26 sibling sites return any org's documents + PII to any authenticated user. This slice retrofits **strict multi-tenant isolation**: an `Organization` model, `org_id` on all tenant tables, **fail-closed** ORM-level query scoping, a migration backfilling existing data to one default org, and org-binding for the background/config/MCP paths. No account can read across orgs — cross-org leakage becomes structurally impossible.

## Locked decisions

| Decision | Choice |
|----------|--------|
| Scope | **Isolation-first** — model + scoping + backfill to one default org; DEFER self-serve org creation / signup / onboarding UI |
| Cross-org access | **Strict isolation** — no cross-org actor; every user (incl. admin) confined to their org; existing bootstrap admin → default-org admin; roles `admin/reviewer/viewer` stay, org-scoped |
| Enforcement mechanism | **Session-level `with_loader_criteria`** (fail-closed) via a shared `_TenantMixin`, one listener in `database.py`; `current_org()` **raises when unset** |
| Child tables | **Denormalize `org_id`** onto `ExtractedField` + `AuditLog` (criteria needs a column on the loaded entity; covers `serialize()` lazy loads) |
| Config tables | `SchemaDefinition`/`WebhookConfig`/`AutoApproveConfig` get `org_id` + composite `(org_id, key/document_type)` uniques; builtins stay global |
| Org membership | Single org per user (`User.org_id`); no multi-org membership |
| Org creation | Manual (migration seeds default; `bootstrap_admin` get-or-creates) — no CRUD endpoint this slice |
| MCP | Bind to `DEFAULT_ORG_ID` for now (dormant); per-token org binding is a flagged fast-follow |

## Non-goals (this slice)

- Self-serve org creation, signup/invite flow, org-switcher, onboarding UI.
- A cross-org platform/superadmin role or platform-wide ops console (System Health/metrics become **org-scoped**).
- Per-token MCP org binding (MCP is dormant; hardcode default org, flag fast-follow).
- Raw-SQL/reporting scoping (none exists today — keep it that way; any future `text()` on a tenant table needs a manual review item).

---

## Data model

New `Organization` (`organizations`): `id` PK, `name: str`, `created_at`. Seed id **1** = "Default Organization".

New mixin `_TenantMixin` (in `models.py`): adds `org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)`. Applied to: `User`, `Document`, `ExtractedField`, `AuditLog`, `SchemaDefinition`, `WebhookConfig`, `AutoApproveConfig`.

Constraint changes (global-unique → unique-per-org):
- `schema_definitions`: `key` unique → `UniqueConstraint(org_id, key)`.
- `webhook_configs`: `document_type` unique index → `Index(org_id, document_type, unique=True)`.
- `auto_approve_configs`: `document_type` unique index → `Index(org_id, document_type, unique=True)`.

`ExtractedField.org_id` / `AuditLog.org_id` are denormalized from the parent `Document` and stamped at write; the FK to `documents.id` remains the source of truth (a wrong-org write would be a bug — covered by tests).

## Enforcement mechanism (the core)

**`backend/app/context.py`** (new): a `ContextVar[int | None]` + `set_current_org(org_id)` and `current_org_id()` that **raises `RuntimeError` when unset** (fail-closed — SQLAlchemy propagates it out of query execution → the existing error middleware returns 500, never leaks rows).

**`backend/app/database.py`**: register one `do_orm_execute` listener that, for SELECTs not tagged `skip_org_filter`, applies `with_loader_criteria(_TenantMixin, lambda cls: cls.org_id == current_org_id(), include_aliases=True)`. Registering against the shared mixin means one seam covers every tenant model, and a future tenant table is protected simply by inheriting `_TenantMixin` (visible in the model file). This auto-scopes `.query()`, `Session.get()` (→ free cross-org **404** on every by-id endpoint, including the `PATCH /users/{id}` IDOR), and **lazy relationship loads** (`serialize()`'s `doc.extracted_fields`/`doc.audit_logs`).

**Request path:** `create_access_token` adds `org_id` to JWT claims; `auth.get_current_user` calls `set_current_org(claims["org_id"])` **before** its `db.get(User, ...)` (now itself scoped; add `user.org_id == claims["org_id"]` as defense-in-depth), and resets in a `finally`. The pre-auth login lookup (`db.query(User).filter_by(email=...)`) is the **one** audited escape hatch: `.execution_options(skip_org_filter=True)`.

**Background paths** (own `SessionLocal()`, no request context — do **not** rely on contextvar propagation): thread `org_id` explicitly and `set_current_org(org_id)` first thing.
- `jobs.run_pipeline_task(document_id, actor_id, org_id)` — caller passes `doc.org_id`.
- `webhooks.deliver_webhook(config_id, document_id, approved_by, org_id)` — callers (`update_document`, `pipeline._maybe_auto_approve`) pass `doc.org_id`.
- MCP tools (`mcp_server.build_mcp`): `set_current_org(config.DEFAULT_ORG_ID)` at the top of each tool for now (dormant); per-token org binding flagged as fast-follow.
- One legitimate global op: `reset_stuck_processing` (startup) operates across orgs — it uses `skip_org_filter` explicitly (audited).

## Hidden cross-org leaks (must-fix; found by the Risk lens)

Most are auto-closed once the columns + criteria + org-context exist; belt-and-suspenders where a reviewer looks:
- **Webhook by `document_type` only** (`update_document`, `_maybe_auto_approve`) → auto-scoped (`WebhookConfig.org_id` + criteria + org context set). Without this, an approved doc's PII payload could POST to the wrong org's URL.
- **Auto-approve by `document_type` only** (`should_auto_approve`) → auto-scoped.
- **`services.get_correction_hints`** (learning loop, pipeline context) → **explicit** signature `get_correction_hints(db, org_id, document_type, fields)` + explicit `Document.org_id == org_id` filter (in addition to ambient criteria) — the highest-risk copy-paste function; caller `extractor.py` passes `ctx.document.org_id`.
- **`eval.correction_records`** (`/auto-approve/eval/{type}`) → auto-scoped via criteria (runs in request context).
- **`PATCH /users/{id}` cross-org IDOR** → auto-fixed (`User.org_id` + criteria → `db.get` returns None → existing 404).

## Write-path stamping

`org_id` is always server-derived, **never** client-supplied:
- `upload` and MCP `extract_document_impl`: `Document(org_id=current_user.org_id / principal.org_id, ...)`.
- Pipeline `ExtractedField(org_id=document.org_id, ...)`; `services.log(...)` stamps `AuditLog.org_id` from the document (thread `org_id` through `log()` or read off the doc).
- Config creates (`POST /schemas|/webhooks|/auto-approve`): stamp `org_id=current_user.org_id`; the 409 dup-checks become per-org.

## Migration `0009` (vetted by Migration/data-ops)

One hand-written file (matching the `0008` `batch_alter_table` style), chaining from `0008_document_delivery_flags`:
1. `create_table("organizations", ...)`; `INSERT ... VALUES (1, 'Default Organization', CURRENT_TIMESTAMP)` (explicit literal id).
2. For each of the 7 tenant tables: add `org_id` **nullable** + FK to organizations (batch mode).
3. Backfill: `UPDATE {t} SET org_id = 1 WHERE org_id IS NULL` for each.
4. Replace global uniques with composites: drop `schema_definitions` key-unique → add `uq_schema_definitions_org_id_key`; drop `ix_webhook_configs_document_type` → add unique `ix_webhook_configs_org_document_type` on `(org_id, document_type)`; same for `auto_approve_configs`.
5. Flip every `org_id` to **NOT NULL** last (batch mode) — never NOT-NULL-on-create.
`downgrade` reverses (drop composites/FKs/columns, restore single-column uniques, drop `organizations`) — with a comment that restoring a global unique fails if two orgs later hold colliding keys.

**⚠️ Prod hazard (flagged):** there is no `naming_convention` on `Base.metadata`, so the real Postgres unique-constraint names may differ from Alembic's assumption — **reflect prod (`SELECT conname FROM pg_constraint …`) and pin the exact drop-target names before running on Neon**, or reflect defensively in the migration. Getting it wrong fails safe (constraint-not-found), but pin it beforehand. Small data volume → lock duration is a non-issue; **ordering** (nullable→backfill→NOT NULL) is the safety mechanism.

## Bootstrap & config

- `config.DEFAULT_ORG_ID = int(os.getenv("DEFAULT_ORG_ID", "1"))`.
- `bootstrap_admin`: get-or-create `Organization(DEFAULT_ORG_ID)` (idempotent) and create the admin with `org_id=DEFAULT_ORG_ID`; change its "any users yet" guard from a global `count()` to **scoped by org** (else a second org's bootstrap would no-op forever).
- `create_user(...)` gains an `org_id` parameter; `POST /users` stamps the creating admin's `org_id` (an admin only creates users in their own org).

## Error handling

- Missing org context on any ORM SELECT → `current_org_id()` raises → 500 (fail-closed; never a silent unscoped read). This is intentional — a bug surfaces loudly rather than leaking.
- Cross-org access by id → row not found under criteria → existing `if not X: 404` (existence not disclosed; 404 not 403).
- Client-supplied `org_id` is ignored everywhere (server-derived only).

## Testing (offline)

**The isolation matrix is the deliverable.** Two orgs (A, B), each with a user + a document + custom schema + webhook + auto-approve config; org B seeded with data. Assert from org A's token:
- `GET /documents` → 0 of B's docs; `GET|PUT /document/{B}`, `POST /process/{B}`, `GET /export/{B}` → 404.
- `PATCH|POST(approve)|DELETE /schemas/{B}`, `PATCH|DELETE /webhooks/{B}`, `PATCH|DELETE /auto-approve/{B}` → 404.
- `PATCH /users/{B}` → 404 (no cross-org user mutation).
- `GET /documents/stats`, `GET /admin/metrics` → reflect org A only.
- **Fail-closed unit test:** a scoped query with no org context set **raises** (does not return rows).
- **Pipeline/background org-scoping:** org B has approved `invoice` corrections → org A processing a new invoice gets `get_correction_hints` with **zero** B values; org A approve fires only org A's webhook (not B's same-type webhook); org B's low auto-approve floor does **not** auto-approve org A's doc.
- **MCP:** an org-A-bound tool call can't reach a B document; `extract_document` stamps `org_id=A`.
- **Migration 0009:** upgrade 0008→head on fresh SQLite; a pre-seeded pre-0009 row gets `org_id=1`; `organizations` has id 1; composite uniques exist; downgrade round-trips.
- Existing fixtures create users/docs without `org_id` — update them (a shared default-org test fixture). Keep the suite green (currently backend 249 / frontend 40), net new on top.

## Build sequencing (one plan, ordered independently-reviewable tasks)

1. `Organization` model + `_TenantMixin` + `org_id` on all 7 tables + composite uniques (model side) + migration `0009` + `config.DEFAULT_ORG_ID` + migration test.
2. `context.py` (contextvar, fail-closed `current_org_id`) + `database.py` `do_orm_execute` loader-criteria listener + fail-closed unit test + `skip_org_filter` escape hatch.
3. Auth wiring: `org_id` in JWT claims; `get_current_user` sets/resets org context; `login` uses `skip_org_filter`; `bootstrap_admin` + `create_user(org_id=...)` + `POST /users` stamping.
4. Write-path stamping: `upload` stamps `Document.org_id`; pipeline stamps `ExtractedField`/`AuditLog` org_id; config POSTs stamp org_id + per-org 409 dup-checks.
5. Background contexts: thread `org_id` into `run_pipeline_task` / `deliver_webhook` / MCP tools + `set_current_org`; `get_correction_hints` explicit org filter; `reset_stuck_processing` uses `skip_org_filter`.
6. Two-org isolation test matrix + existing-fixture updates (the security proof).
7. Docs: `.env.example` (`DEFAULT_ORG_ID`), `docs/deployment.md` (tenancy + migration 0009 + prod constraint-name caveat + MCP fast-follow), `docs/roadmap.md`.

## Deployment notes

Migration `0009` runs via `alembic upgrade head` (pin the real Postgres constraint names first — see the hazard above). New optional env `DEFAULT_ORG_ID` (default 1). No new dependencies. JWTs issued before this deploy lack an `org_id` claim → treat a missing claim as invalid (force re-login) rather than defaulting (fail-closed). MCP stays dormant and bound to the default org; per-token org binding must land before enabling MCP for a second org.
