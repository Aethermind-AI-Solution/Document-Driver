# Phase 1 — Persistence & Identity Design

**Date:** 2026-08-07
**Status:** Approved design (pre-plan)
**Roadmap items:** B6 (persistent storage) + B5 (auth + RBAC + actor-stamped audit), scoped as one spec.

## Goal

Move Aethermind off ephemeral SQLite + local disk onto durable storage, and add real user identity: authentication, role-based access control, and an audit trail that records *who* did each action. Persistence and identity are designed as separable units and implemented in that order.

## Platform decisions (locked)

| Concern | Choice | Notes |
|---------|--------|-------|
| Database (prod) | **Neon Postgres** | free tier, no expiry; `postgresql+psycopg://…` |
| Database (dev/test) | **SQLite** | fast, offline; unchanged conftest |
| Migrations | **Alembic** | replaces `create_all` in prod; models remain source of truth |
| Object storage (prod) | **Cloudflare R2** | free 10GB, S3-compatible via boto3 |
| Object storage (dev/test) | **LocalStorage** | files under `UPLOAD_DIR` |
| Auth | **Own FastAPI JWT** | no third-party auth service |
| Password hashing | **argon2** (passlib) | modern; no bcrypt 72-byte limit |
| JWT | **HS256, ~12h single access token** | no refresh token (YAGNI); re-login on expiry |
| Roles | **admin / reviewer / viewer** | see authorization map |

## Non-goals (this phase)

- SSO/OAuth (Phase 6), magic-link email, refresh tokens, password-reset-by-email (no email provider — stays $0).
- Multi-tenancy / organizations (single-tenant for now).
- Data migration of existing rows (Render data is ephemeral/empty).

---

## Part A — Persistence (B6)

### A1. Database backend
- Add dependency `psycopg[binary]`. `DATABASE_URL` already drives the engine; prod sets a Neon `postgresql+psycopg://` URL. Dev/test keep SQLite.
- The Phase-0 SQLite PRAGMA listener (`enable_sqlite_pragmas`) already guards on `get_backend_name().startswith("sqlite")` → no-op on Postgres. No change needed.

### A2. Migrations (Alembic)
- Add `backend/alembic/` + `alembic.ini`. `env.py` imports `Base.metadata` (target metadata) and reads `DATABASE_URL` from `app.config` (not a hardcoded URL).
- **Migration 1** — snapshot current schema: `documents`, `extracted_fields` (including Phase-0 `original_value`), `audit_logs`, `schema_definitions`.
- **Migration 2** — add identity: `users` table; `audit_logs.actor_id` (FK, nullable) + `audit_logs.actor_email`.
- Prod deploy runs `alembic upgrade head` (Render start command / build step). `app.main` no longer calls `Base.metadata.create_all` in prod.
- **Tests** keep `Base.metadata.create_all` on SQLite (fast, offline). Models are the single source of truth, so `create_all` and the Alembic head must agree; a lightweight check (schema diff or a smoke test that upgrades a scratch SQLite/Postgres) guards drift.

### A3. Object storage abstraction
- New module `backend/app/storage.py` with a `Storage` protocol:
  - `save(key: str, data: bytes) -> str` — persist bytes, return the key.
  - `open(key: str) -> bytes` — fetch bytes.
  - `delete(key: str) -> None` — remove (used later by delete/archive).
- **`LocalStorage`** — writes under `UPLOAD_DIR`; default for dev/test.
- **`S3Storage`** — boto3 client to R2 (`R2_ENDPOINT`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`).
- Backend selected in `config` (`STORAGE_BACKEND=local|s3`, default `local`; `s3` requires R2 creds or fails fast at startup).
- A `get_storage()` accessor returns the configured singleton (dependency-injectable, overridable in tests).

### A4. Wiring extraction to storage
- `Document.stored_path` now holds a **storage key**, not a filesystem path (semantics change; column type unchanged).
- `/upload`: `key = storage.save(safe_name, data)`; `Document(stored_path=key)`.
- `process_document`: fetch `data = storage.open(doc.stored_path)`, write to a `NamedTemporaryFile`, pass the temp path to the existing `extract_text`/`ai_extract` (smallest diff — extraction internals untouched), delete the temp file in a `finally`.

---

## Part B — Identity (B5)

### B1. Models
```python
class User(Base):
    id: int PK
    email: str unique, indexed
    password_hash: str
    role: str            # "admin" | "reviewer" | "viewer"
    is_active: bool = True
    created_at: datetime = _utcnow   # reuse Phase-0 tz-aware helper
```
- `AuditLog` gains `actor_id: int | None` (FK → users.id, nullable for system/unauthenticated actions) and `actor_email: str | None` (snapshot, survives user deletion).

### B2. Password + JWT mechanics
- `backend/app/auth.py`:
  - `hash_password(pw)` / `verify_password(pw, hash)` via `passlib` (argon2).
  - `create_access_token(user)` → HS256 JWT with `sub=user.id`, `email`, `role`, `exp` (~12h), signed with `JWT_SECRET` (config).
  - `decode_token(token)` → claims or raises.
- Config adds `JWT_SECRET` (required in prod; a dev default is fine for local), `JWT_EXPIRE_HOURS=12`.

### B3. Dependencies
- `get_current_user` — reads `Authorization: Bearer`, decodes JWT, loads the user, rejects if missing/inactive (401).
- `require_role(*roles)` — returns a dependency that 403s if `current_user.role` not in `roles`.
- These replace the `DEMO_ACCESS_TOKEN` gate (`require_access`), which is removed along with its config.

### B4. Endpoints
- `POST /auth/login` — public; body `{email, password}` → `{access_token, token_type, user}`. Bad creds → 401.
- `GET /auth/me` — any authenticated user → current user.
- `POST /auth/change-password` — any authenticated user changes own password.
- `GET /users` / `POST /users` / `PATCH /users/{id}` — **admin only**; create (email + role + initial password), list, deactivate/change-role. Duplicate email → 409.
- Existing endpoints gain role guards per the map below, and mutating endpoints stamp the actor into the audit log.

### B5. Authorization map
| Endpoint | admin | reviewer | viewer |
|---|:--:|:--:|:--:|
| `POST /auth/login`, `GET /auth/me`, `POST /auth/change-password` | ✓ | ✓ | ✓ |
| `GET /documents`, `GET /document/{id}`, `GET /export/{id}` | ✓ | ✓ | ✓ (read-only) |
| `GET /schemas` | ✓ | ✓ | ✓ |
| `POST /upload`, `POST /process/{id}` | ✓ | ✓ | ✗ |
| `PUT /document/{id}` (edit/approve/reject) | ✓ | ✓ | ✗ |
| `POST /schemas` | ✓ | ✗ | ✗ |
| `GET/POST/PATCH /users` | ✓ | ✗ | ✗ |

### B6. Bootstrap admin
- On startup (or a one-shot migration/seed), if no users exist and `ADMIN_EMAIL`/`ADMIN_PASSWORD` are set, create the first admin. Solves the chicken-and-egg so someone can log in and invite others. Idempotent (skips if any user exists).

### B7. Actor-stamped audit
- `log(...)` gains an optional `actor` (the current user); it records `actor_id` + `actor_email`. Every mutating endpoint (upload, process, edit/approve/reject, schema create, user management) passes the current user. `serialize` audit entries include actor_email.

---

## Part C — Frontend

- **Login page** replacing the demo-token `LoginGate`: email + password → store JWT (localStorage, same slot pattern as today) → send `Authorization: Bearer`. `lib/api.ts` swaps `X-Access-Token` for `Bearer`; a 401 clears the token and returns to login.
- **Role-gated UI:** decode/keep the user (`/auth/me`); Viewers don't see upload/approve/edit controls; user-management and schema-authoring screens are admin-only. The audit panel shows the actor.
- A minimal **user-management screen** (admin) to create/list/deactivate users and set roles.

---

## Error handling

- 401 — missing/expired/invalid token or inactive user. 403 — authenticated but wrong role. 409 — duplicate email/schema key. Storage failures → 502 with a logged cause; boot fails fast if `STORAGE_BACKEND=s3` without R2 creds or if `JWT_SECRET` is unset in prod. Alembic failure stops deploy.

## Testing strategy

- Tests run on SQLite + `LocalStorage` (offline). An **autouse fixture overrides `get_current_user` to a default admin** so the existing 54 tests keep passing with no per-test auth wiring; a helper mints tokens/users for auth-specific tests.
- New coverage: password hash/verify; JWT issue/decode/expiry; login success + bad-creds 401; `get_current_user` rejects inactive/invalid; `require_role` allows and 403-denies per role; admin-only endpoints; actor recorded in audit; bootstrap admin idempotency; `LocalStorage` save/open/delete round-trip; upload→process reads bytes back through storage.
- Keep backend green (54 existing + new) and frontend green (13 existing + login/role-gating tests).

## Build sequencing (one spec, ordered tasks)

1. **Persistence:** Alembic scaffold + migration 1 → psycopg/Postgres config → `storage.py` (Local + S3) + `get_storage` → wire `/upload` + `process_document` to storage.
2. **Identity:** `User` model + migration 2 (users + audit actor) → `auth.py` (hashing + JWT) → `get_current_user` / `require_role` → auth endpoints → RBAC guards on existing endpoints → actor-stamped audit → bootstrap admin → remove `DEMO_ACCESS_TOKEN` gate.
3. **Frontend:** login page + Bearer in `api.ts` + 401 handling → role-gated controls → admin user-management screen.

## Deployment notes

- Render backend: add `alembic upgrade head` to the start/build; new env — `DATABASE_URL` (Neon), `STORAGE_BACKEND=s3` + R2 creds, `JWT_SECRET`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`. Remove `DEMO_ACCESS_TOKEN`.
- `render.yaml` buildFilter already scopes backend redeploys to `backend/**` — unchanged.
- Frontend (Vercel): no new env beyond the existing API base URL.
