# Demo Access Gate + Abuse Guardrails (B2) — Design

**Date:** 2026-07-24
**Component:** Aethermind — backend + frontend
**Status:** Approved (design)

## Problem

The demo backend (`https://…onrender.com`) is completely open: anyone with the
URL can upload, process, edit, and read every document. The frontend is a public
Vercel page, so obscurity is not protection. For a shared feedback demo we need
lightweight access control plus abuse guardrails — without building full user
accounts (that is the larger auth item, deferred).

## Goal

- **Gate:** require a single shared password to use the demo. The backend rejects
  any request without it (except the health check).
- **Guardrails:** cap upload size and rate-limit the expensive endpoints so nobody
  can spam or fill the ephemeral disk.

## Decisions (from brainstorming)

- **Both** a password gate and the guardrails.
- Single **shared password** (no per-user accounts/roles — deferred to the auth item).
- Password lives only as a backend env var + what each visitor types; never baked
  into the frontend bundle.
- Gate **defaults OFF** when the env var is empty, so local dev and tests are
  unaffected and nothing breaks if it is not set.

## Non-goals (YAGNI)

- No user accounts, roles, sessions, or JWT — one shared token only.
- No Redis/distributed rate limit — in-memory is fine (Render free = single instance).
- No brute-force lockout beyond the rate limit.
- No change to the existing review/extraction logic.

## Backend design

### Config (`backend/app/config.py`)

```python
DEMO_ACCESS_TOKEN = os.getenv("DEMO_ACCESS_TOKEN", "")   # empty ⇒ gate disabled
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))
RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX", "20"))       # requests
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60")) # seconds
```

### Security module (`backend/app/security.py`) — the unit-tested core

- **`require_access(request, x_access_token)`** — a FastAPI dependency:
  - If `config.DEMO_ACCESS_TOKEN` is empty → allow (gate disabled).
  - If `request.url.path == "/health"` → allow (keeps the UptimeRobot pinger working).
  - Else require header `X-Access-Token` to equal the token (constant-time
    `secrets.compare_digest`); otherwise `raise HTTPException(401, "Invalid or missing access token")`.
  - Reads `config.DEMO_ACCESS_TOKEN` **at call time** (via `from . import config`)
    so tests can monkeypatch it.

- **`SlidingWindowRateLimiter`** — pure, testable:
  ```python
  class SlidingWindowRateLimiter:
      def __init__(self): self.hits = defaultdict(list)
      def allow(self, key: str, now: float, max_requests: int, window: float) -> bool: ...
  ```
  Prunes timestamps older than `now - window`; returns `False` if the count is
  already `>= max_requests`, else records `now` and returns `True`. `now` is passed
  in (no hidden clock) so it is deterministic under test.

- **`rate_limit(request)`** — a FastAPI dependency used on the expensive routes:
  derives the client IP from `X-Forwarded-For` (Render is behind a proxy), falling
  back to `request.client.host`; calls the module-level limiter with
  `time.time()`, `config.RATE_LIMIT_MAX`, `config.RATE_LIMIT_WINDOW`; raises
  `HTTPException(429, "Too many requests, please slow down")` when not allowed.

### Wiring (`backend/app/main.py`)

- Apply the gate app-wide: `app = FastAPI(..., dependencies=[Depends(require_access)])`.
  (Because `require_access` skips `/health` and no-token mode, one line covers every
  route; CORS preflight `OPTIONS` is handled by `CORSMiddleware` before route
  dependencies run, and a 401 raised as `HTTPException` still gets CORS headers.)
- Add `dependencies=[Depends(rate_limit)]` to **`POST /upload`** and
  **`POST /process/{id}`** only.
- In `POST /upload`, enforce the size cap: read the bytes and if
  `len(data) > config.MAX_UPLOAD_MB * 1024 * 1024` → `raise HTTPException(413, "File exceeds the N MB limit")`;
  otherwise write those bytes to disk (replaces the current `shutil.copyfileobj`).
  Read the limit via the `config` module attribute at call time (`from . import config`)
  so tests can monkeypatch it.

## Frontend design (`frontend/app/`)

The frontend **auto-detects** whether a gate is active, so it never forces a login
when the backend gate is off.

- **`lib/api.ts`:** attach `X-Access-Token` from `localStorage["aethermind_token"]`
  when present. On a `401` response, remove the stored token and throw an error
  carrying `status: 401` (so callers can react); on other non-OK responses, throw as today.
- **`app/page.tsx`:** add a `needsAuth` state.
  - On initial load, `refresh()` calls the API. If it throws a `401`, set
    `needsAuth = true`. If it succeeds (gate off, or a valid token is stored), show the app.
  - When `needsAuth`, render a centered **login card** ("Enter demo access password")
    instead of the dashboard. On submit: store the entered value as the token, set
    `needsAuth = false`, and re-run `refresh()`. If it 401s again, show an inline
    "Incorrect password" message and stay on the card.
- No `NEXT_PUBLIC` token; nothing secret ships in the bundle.

## Deploy

- Set **`DEMO_ACCESS_TOKEN`** on Render to a chosen password → share "link + password".
- Optionally override `MAX_UPLOAD_MB` / `RATE_LIMIT_MAX` / `RATE_LIMIT_WINDOW`.
- No frontend redeploy needed for the gate (token is entered by the visitor); the
  frontend code change (login card) ships once via the normal Vercel deploy.

## Error handling

- Missing/wrong token → `401` with `{"detail": "..."}` (CORS headers present so the
  browser can read it and show the login card).
- Oversized upload → `413`. Rate exceeded → `429`. Both JSON `detail`.
- Gate disabled (empty env) → all requests pass, exactly as today (local dev/tests).

## Testing

### Backend (pytest, existing harness)
- **Unit — `require_access`:** empty token ⇒ allows; token set + missing/wrong header
  ⇒ 401; correct header ⇒ passes; `/health` path ⇒ allowed even without header.
  (Monkeypatch `config.DEMO_ACCESS_TOKEN`.)
- **Unit — `SlidingWindowRateLimiter.allow`:** allows up to `max`, blocks the next,
  and allows again after the window (using injected `now`); per-key isolation.
- **Endpoint (`TestClient`):** with `DEMO_ACCESS_TOKEN` set (monkeypatched) —
  `/documents` without header ⇒ 401, with the correct header ⇒ 200; `/health` without
  header ⇒ 200. Oversized upload ⇒ 413 (monkeypatch `config.MAX_UPLOAD_MB` small).
  Rate limit: with the gate off, monkeypatch `config.RATE_LIMIT_MAX` to a tiny number
  and repeatedly `POST /upload` a small valid file — the first N succeed (201), the
  next ⇒ 429. Reset the module-level limiter's state at the start of that test so it
  is deterministic. With the token empty ⇒ everything passes (regression: existing
  tests still green).

### Frontend (Vitest + RTL, existing harness)
- Gate active: mock `api` to throw `401` on first load ⇒ the login card renders; after
  "logging in" (mock then returns data) ⇒ the dashboard renders.
- Regression: existing `review-close` / `review-error` tests (gate off) still pass.

## Rollback

Revert the feature commits. Leaving `DEMO_ACCESS_TOKEN` unset restores fully-open
behavior, so the gate can also be disabled at runtime without a code change.
