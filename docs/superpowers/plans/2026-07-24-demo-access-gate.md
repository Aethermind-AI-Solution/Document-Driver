# Demo Access Gate + Abuse Guardrails (B2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate the demo behind a shared password and add upload-size + rate-limit guardrails, without building user accounts.

**Architecture:** A pure `security.py` module provides an app-wide `require_access` dependency (skips `/health`, disabled when the token env is empty) and a `SlidingWindowRateLimiter`; `main.py` wires the gate app-wide, rate-limits `/upload` + `/process`, and caps upload size. The frontend auto-detects the gate (401 → login card) and sends the token header.

**Tech Stack:** FastAPI, pytest; Next.js/React, Vitest + RTL.

## Global Constraints

- Gate **defaults OFF** when `DEMO_ACCESS_TOKEN` is empty — local dev and all existing tests must stay green.
- Single shared token (no accounts/roles/JWT). In-memory rate limit. No new backend deps.
- `/health` is always reachable without a token (UptimeRobot pinger).
- Token is never baked into the frontend bundle (no `NEXT_PUBLIC` token).
- Config values read at call time via the `config` module so tests can monkeypatch: `DEMO_ACCESS_TOKEN`, `MAX_UPLOAD_MB`, `RATE_LIMIT_MAX`, `RATE_LIMIT_WINDOW`, `UPLOAD_DIR`.
- Defaults: `MAX_UPLOAD_MB=10`, `RATE_LIMIT_MAX=20`, `RATE_LIMIT_WINDOW=60`.
- Backend tests run as `cd backend && source .venv/bin/activate && python -m pytest`.

---

### Task 1: Backend security module (gate + rate limiter), TDD

**Files:**
- Modify: `backend/app/config.py`
- Create: `backend/app/security.py`
- Test: `backend/tests/test_security.py`

**Interfaces (produced for Task 2):**
- `require_access(request, x_access_token)` — FastAPI dependency; raises `HTTPException(401)` when a token is configured and the `X-Access-Token` header is missing/wrong; allows `/health` and the empty-token (disabled) case.
- `class SlidingWindowRateLimiter` with `allow(key, now, max_requests, window) -> bool` and `reset()`.
- `rate_limiter` (module-level instance) and `rate_limit(request)` dependency; raises `HTTPException(429)` when over the limit.

- [ ] **Step 1: Add config values**

In `backend/app/config.py`, add after the `CORS_ORIGINS` line:

```python
DEMO_ACCESS_TOKEN = os.getenv("DEMO_ACCESS_TOKEN", "")   # empty ⇒ gate disabled
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))
RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX", "20"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_security.py`:

```python
import pytest
from fastapi import HTTPException, Request
from app import config
from app.security import require_access, SlidingWindowRateLimiter


def _request(path):
    return Request({"type": "http", "http_version": "1.1", "method": "GET",
                    "path": path, "raw_path": path.encode(), "headers": [],
                    "query_string": b"", "scheme": "http", "server": ("test", 80),
                    "client": ("1.2.3.4", 1234)})


def test_gate_disabled_allows(monkeypatch):
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "")
    require_access(_request("/documents"), None)  # no exception


def test_gate_blocks_without_token(monkeypatch):
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "secret")
    with pytest.raises(HTTPException) as e:
        require_access(_request("/documents"), None)
    assert e.value.status_code == 401


def test_gate_blocks_wrong_token(monkeypatch):
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "secret")
    with pytest.raises(HTTPException):
        require_access(_request("/documents"), "nope")


def test_gate_allows_correct_token(monkeypatch):
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "secret")
    require_access(_request("/documents"), "secret")  # no exception


def test_gate_allows_health_without_token(monkeypatch):
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "secret")
    require_access(_request("/health"), None)  # no exception


def test_rate_limiter_allows_then_blocks():
    rl = SlidingWindowRateLimiter()
    assert rl.allow("ip", 100.0, 2, 60) is True
    assert rl.allow("ip", 100.5, 2, 60) is True
    assert rl.allow("ip", 101.0, 2, 60) is False


def test_rate_limiter_recovers_after_window():
    rl = SlidingWindowRateLimiter()
    assert rl.allow("ip", 100.0, 1, 60) is True
    assert rl.allow("ip", 130.0, 1, 60) is False   # still inside the window
    assert rl.allow("ip", 200.0, 1, 60) is True    # window elapsed


def test_rate_limiter_per_key_isolation():
    rl = SlidingWindowRateLimiter()
    assert rl.allow("a", 100.0, 1, 60) is True
    assert rl.allow("b", 100.0, 1, 60) is True      # different key unaffected
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_security.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.security'`.

- [ ] **Step 4: Implement the module**

Create `backend/app/security.py`:

```python
import secrets, time
from collections import defaultdict
from fastapi import Header, HTTPException, Request
from . import config


def require_access(request: Request, x_access_token: str | None = Header(default=None)):
    """Gate every route (except /health) behind a shared token when one is configured."""
    if not config.DEMO_ACCESS_TOKEN:
        return
    if request.url.path == "/health":
        return
    if not (x_access_token and secrets.compare_digest(x_access_token, config.DEMO_ACCESS_TOKEN)):
        raise HTTPException(401, "Invalid or missing access token")


class SlidingWindowRateLimiter:
    def __init__(self):
        self.hits: dict[str, list[float]] = defaultdict(list)

    def allow(self, key: str, now: float, max_requests: int, window: float) -> bool:
        recent = [t for t in self.hits[key] if t > now - window]
        if len(recent) >= max_requests:
            self.hits[key] = recent
            return False
        recent.append(now)
        self.hits[key] = recent
        return True

    def reset(self):
        self.hits.clear()


rate_limiter = SlidingWindowRateLimiter()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request):
    if not rate_limiter.allow(_client_ip(request), time.time(),
                              config.RATE_LIMIT_MAX, config.RATE_LIMIT_WINDOW):
        raise HTTPException(429, "Too many requests, please slow down")
```

- [ ] **Step 5: Run to verify they pass**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_security.py -q`
Expected: PASS — 8 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/config.py backend/app/security.py backend/tests/test_security.py
git commit -m "feat: add access-gate + rate-limiter security module

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Wire gate + guardrails into the API

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_security_endpoints.py`

**Interfaces:**
- Consumes: `require_access`, `rate_limit`, `rate_limiter` (Task 1); `db_session`/`client` fixtures.
- Produces: gated API; `/upload` + `/process` rate-limited; `/upload` size-capped.

- [ ] **Step 1: Write the failing endpoint tests**

Create `backend/tests/test_security_endpoints.py`:

```python
import io
from app import config
from app.security import rate_limiter


def _file():
    return {"file": ("a.pdf", io.BytesIO(b"%PDF-1.4 minimal"), "application/pdf")}


def test_gate_blocks_documents_without_token(client, db_session, monkeypatch):
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "secret")
    assert client.get("/documents").status_code == 401


def test_gate_allows_documents_with_token(client, db_session, monkeypatch):
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "secret")
    assert client.get("/documents", headers={"X-Access-Token": "secret"}).status_code == 200


def test_health_open_without_token(client, db_session, monkeypatch):
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "secret")
    assert client.get("/health").status_code == 200


def test_upload_size_limit(client, db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "")
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 0)      # any non-empty file is too big
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path)
    rate_limiter.reset()
    files = {"file": ("big.pdf", io.BytesIO(b"x" * 1024), "application/pdf")}
    assert client.post("/upload?document_type=invoice", files=files).status_code == 413


def test_rate_limit_blocks_after_max(client, db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "")
    monkeypatch.setattr(config, "RATE_LIMIT_MAX", 2)
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path)
    rate_limiter.reset()
    assert client.post("/upload?document_type=invoice", files=_file()).status_code == 201
    assert client.post("/upload?document_type=invoice", files=_file()).status_code == 201
    assert client.post("/upload?document_type=invoice", files=_file()).status_code == 429
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_security_endpoints.py -q`
Expected: FAIL — gate/limits not wired yet (401/413/429 assertions fail; e.g. `/documents` returns 200 without a token).

- [ ] **Step 3: Update `main.py` imports**

In `backend/app/main.py`, change the config import line (line 8):

```python
from .config import CORS_ORIGINS
```

and add these two import lines right after it:

```python
from . import config
from .security import rate_limit, require_access
```

(Do not keep `UPLOAD_DIR` in the `from .config import` line — `/upload` will use `config.UPLOAD_DIR` instead.)

- [ ] **Step 4: Apply the gate app-wide**

Replace line 15:

```python
app = FastAPI(title="Document Intelligence Engine", version="1.0.0")
```

with:

```python
app = FastAPI(title="Document Intelligence Engine", version="1.0.0", dependencies=[Depends(require_access)])
```

- [ ] **Step 5: Rate-limit + size-cap `/upload`**

Change the `/upload` decorator to add the rate-limit dependency:

```python
@app.post("/upload", status_code=201, dependencies=[Depends(rate_limit)])
```

Then, inside `upload(...)`, replace these three lines:

```python
    safe_name = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{Path(file.filename).name}"
    destination = UPLOAD_DIR / safe_name
    with destination.open("wb") as out: shutil.copyfileobj(file.file, out)
```

with:

```python
    data = await file.read()
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024: raise HTTPException(413, f"File exceeds the {config.MAX_UPLOAD_MB} MB limit")
    safe_name = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{Path(file.filename).name}"
    destination = config.UPLOAD_DIR / safe_name
    destination.write_bytes(data)
```

- [ ] **Step 6: Rate-limit `/process`**

Change the `/process` decorator (line 49):

```python
@app.post("/process/{document_id}", dependencies=[Depends(rate_limit)])
```

- [ ] **Step 7: Run the full backend suite**

Run: `cd backend && source .venv/bin/activate && python -m pytest -q`
Expected: PASS — all previous tests still green **plus** the 5 new endpoint tests (existing tests are unaffected because `DEMO_ACCESS_TOKEN` defaults to empty). `shutil` is now unused in `main.py`; leave the import (harmless) or remove it — either is fine.

- [ ] **Step 8: Commit**

```bash
git add backend/app/main.py backend/tests/test_security_endpoints.py
git commit -m "feat: gate API and rate-limit/size-cap uploads

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Frontend login gate + token header

**Files:**
- Modify: `frontend/lib/api.ts`
- Modify: `frontend/app/page.tsx`
- Modify: `frontend/app/review-close.test.tsx`, `frontend/app/review-error.test.tsx` (update mocks)
- Test: `frontend/app/review-gate.test.tsx`

**Interfaces:**
- `api.ts` exports `getToken`, `setToken`, `clearToken`, and an `api()` that sends `X-Access-Token` and throws an error with `.status` (401 clears the token).

- [ ] **Step 1: Update `lib/api.ts`**

Replace the entire contents of `frontend/lib/api.ts` with:

```ts
const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const TOKEN_KEY = "aethermind_token";

export function getToken() { return typeof window !== "undefined" ? window.localStorage.getItem(TOKEN_KEY) || "" : ""; }
export function setToken(t: string) { if (typeof window !== "undefined") window.localStorage.setItem(TOKEN_KEY, t); }
export function clearToken() { if (typeof window !== "undefined") window.localStorage.removeItem(TOKEN_KEY); }

export async function api(path: string, init: RequestInit = {}) {
  const token = getToken();
  const headers: Record<string, string> = { ...(init.headers as Record<string, string> || {}) };
  if (token) headers["X-Access-Token"] = token;
  const r = await fetch(`${API}${path}`, { ...init, headers });
  if (r.status === 401) { clearToken(); const e: any = new Error("Unauthorized"); e.status = 401; throw e; }
  if (!r.ok) { const e: any = new Error(await r.text()); e.status = r.status; throw e; }
  return r.json();
}
```

- [ ] **Step 2: Write the failing gate test**

Create `frontend/app/review-gate.test.tsx`:

```tsx
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import Home from "./page";

let gated = true;

vi.mock("../lib/api", () => ({
  getToken: () => "",
  setToken: vi.fn(() => { gated = false; }),   // "logging in" opens the gate
  clearToken: vi.fn(),
  api: vi.fn(async (path: string) => {
    if (gated) { const e: any = new Error("Unauthorized"); e.status = 401; throw e; }
    if (path === "/schemas") return [{ key: "invoice", name: "Invoice", fields: [] }];
    if (path === "/documents") return [];
    return {};
  }),
}));

beforeEach(() => { gated = true; cleanup(); });

describe("demo access gate", () => {
  it("shows the login card on 401 and unlocks after entering the password", async () => {
    render(<Home />);
    await screen.findByText(/demo access password/i);
    fireEvent.change(screen.getByPlaceholderText(/password/i), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: /enter/i }));
    await waitFor(() => expect(screen.getByText("Upload a document")).toBeTruthy());
  });
});
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd frontend && npx vitest run app/review-gate.test.tsx`
Expected: FAIL — no login card yet (`getByText(/demo access password/i)` not found; the app renders instead).

- [ ] **Step 4: Add the token imports + auth state in `page.tsx`**

In `frontend/app/page.tsx`, change the api import:

```tsx
import { api } from "../lib/api";
```

to:

```tsx
import { api, setToken } from "../lib/api";
```

Then, inside `Home`, add auth state next to the other `useState` declarations (right after the big destructured `useState` line):

```tsx
 const [needsAuth,setNeedsAuth]=useState(false);const [authError,setAuthError]=useState("");
```

- [ ] **Step 5: Detect 401 in `refresh` and add a `login` handler**

Replace the existing `refresh` line:

```tsx
 const refresh=async()=>{try{setSchemas(await api("/schemas"));setDocs(await api("/documents"))}catch{setMessage("API unavailable — start the FastAPI service to connect the workspace.")}};
```

with:

```tsx
 const refresh=async()=>{try{setSchemas(await api("/schemas"));setDocs(await api("/documents"));setNeedsAuth(false)}catch(e:any){if(e?.status===401){setNeedsAuth(true)}else{setMessage("API unavailable — start the FastAPI service to connect the workspace.")}}};
 const login=async(password:string)=>{setToken(password);setAuthError("");try{await api("/schemas");setNeedsAuth(false);refresh()}catch(e:any){setAuthError(e?.status===401?"Incorrect password":"Could not connect");setNeedsAuth(true)}};
```

- [ ] **Step 6: Render the login card when gated**

In `Home`'s `return`, immediately before `return <main className="min-h-screen">`, add:

```tsx
 if(needsAuth) return <LoginGate onSubmit={login} error={authError}/>;
```

- [ ] **Step 7: Add the `LoginGate` component**

At the end of `frontend/app/page.tsx`, add:

```tsx
function LoginGate({onSubmit,error}:{onSubmit:(p:string)=>void,error:string}){const [pw,setPw]=useState("");return <main className="flex min-h-screen items-center justify-center p-6"><div className="card w-full max-w-sm p-6"><div className="mb-4 flex items-center gap-3"><div className="rounded-lg bg-slate-900 p-2 text-white"><ShieldCheck size={20}/></div><div><h1 className="font-semibold">Aethermind</h1><p className="text-xs text-slate-500">Demo access</p></div></div><p className="mb-3 text-sm text-slate-600">Enter the demo access password to continue.</p><form onSubmit={e=>{e.preventDefault();onSubmit(pw)}}><input type="password" value={pw} onChange={e=>setPw(e.target.value)} placeholder="Password" autoFocus className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"/>{error&&<p className="mt-2 text-sm text-rose-600">{error}</p>}<button type="submit" className="btn-primary mt-4 w-full">Enter</button></form></div></main>}
```

(`ShieldCheck` is already imported in `page.tsx`.)

- [ ] **Step 8: Keep the existing frontend tests green (update their mocks)**

`review-close.test.tsx` and `review-error.test.tsx` mock `../lib/api` with only `api`. Since `page.tsx` now also imports `setToken`, add the extra exports to **both** files' `vi.mock("../lib/api", () => ({ ... }))` object (alongside the existing `api:`):

```ts
  getToken: () => "",
  setToken: () => {},
  clearToken: () => {},
```

- [ ] **Step 9: Run the full frontend suite + typecheck**

Run: `cd frontend && npx tsc --noEmit && npm test`
Expected: `tsc` clean; all tests pass — `agent-timeline`, `review-close`, `review-error`, and the new `review-gate`.

- [ ] **Step 10: Commit**

```bash
git add frontend/lib/api.ts frontend/app/page.tsx frontend/app/review-close.test.tsx frontend/app/review-error.test.tsx frontend/app/review-gate.test.tsx
git commit -m "feat: frontend demo access gate with token header

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the implementer

- Match the dense one-line style of `page.tsx`.
- Do not change the review/extraction logic.
- After merge, deploy: set `DEMO_ACCESS_TOKEN` on Render (redeploys backend); the frontend login card ships via the normal Vercel deploy. `/health` stays open for the pinger.
