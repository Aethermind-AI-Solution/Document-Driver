# Deploying Aethermind (Vercel + Render)

This guide deploys the app as a shareable demo URL for **$0**, with no cold-start
hang during demos.

## Architecture

```
 Browser ──▶ Frontend (Next.js)          Backend (FastAPI)
             Vercel — always warm  ──▶   Render free web service
             https://<app>.vercel.app    https://<api>.onrender.com
                                          │
             UptimeRobot / cron-job.org ──┘  pings /health every 10 min
                                             (keeps the free backend awake)
```

- **Why split hosts?** The backend is stateful — SQLite + local `uploads/`/`exports/`
  + PyMuPDF. Vercel only runs Python as serverless functions with an ephemeral
  filesystem, which breaks SQLite and file storage. So the backend needs a real
  always-on process (Render); Vercel hosts the Next.js frontend, which it does best.
- **No secrets required.** Without `OPENAI_API_KEY`, the backend uses deterministic
  fallback extraction — the demo works fully with zero credentials.

## The cold-start problem and the free fix

Render's **free** web service sleeps after ~15 minutes of no traffic; the next
request then takes ~30–50s to wake. Fix it for free by pinging the backend's
`/health` endpoint every 10 minutes with a free uptime monitor
(**UptimeRobot** or **cron-job.org**). That keeps it permanently awake, so real
visitors never hit a cold start. A single always-pinged service uses ~730 of
Render's 750 free instance-hours/month — just under the cap.

When you have budget: switch the Render service to the **Starter ($7/mo)** plan
(no spin-down) and delete the pinger. Nothing else changes.

## Code/config changes made for deployment

These are already in the repo:

| Change | File | Why |
|--------|------|-----|
| CORS origins read from `CORS_ORIGINS` env var (was hardcoded localhost) | `backend/app/config.py`, `backend/app/main.py` | Allow the deployed Vercel domain to call the API |
| SQLite DB directory auto-created on startup | `backend/app/config.py` | Fresh deploys start with an empty tree |
| Render Blueprint | `render.yaml` | One-click backend deploy |
| Expanded ignore rules (`__pycache__`, `.venv`, `.next`, `.env`, DB/uploads, `.superpowers/`) | `.gitignore` | Keep build artifacts, deps, secrets, and generated data out of git |
| Tracked empty data dirs | `database/.gitkeep`, `uploads/.gitkeep`, `exports/.gitkeep` | Dirs exist on a fresh clone/deploy |

The frontend needs **no code change** — it already reads the API base from
`NEXT_PUBLIC_API_URL` (`frontend/lib/api.ts`), which you set in Vercel.

> **Monorepo note:** this repository also contains an unrelated Telegram-bot
> project. Both hosts are pointed at subfolders (Render `rootDir: backend`,
> Vercel Root Directory `frontend`), so the bot is ignored by both.

## Step-by-step

### 0. Prerequisite — push to GitHub
The local repo has no remote yet. Create an empty GitHub repo, then:
```bash
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main   # or master, whichever this repo uses
```
Confirm `.env` is NOT pushed (it is gitignored): `git ls-files | grep -c '^\.env$'` should print `0`.

### 1. Backend → Render
1. Render dashboard → **New → Blueprint** → connect the GitHub repo. Render reads `render.yaml`.
2. When prompted for env vars, leave `CORS_ORIGINS` and `OPENAI_API_KEY` blank for now → **Apply**.
3. Wait for the first deploy; note the URL, e.g. `https://aethermind-backend.onrender.com`.
4. Verify: open `https://<api>.onrender.com/health` → `{"status":"ok"}`.

### 2. Frontend → Vercel
1. Vercel → **Add New → Project** → import the same repo.
2. Set **Root Directory = `frontend`** (Vercel auto-detects Next.js).
3. Add an Environment Variable:
   - `NEXT_PUBLIC_API_URL = https://<api>.onrender.com`  (your Render URL, no trailing slash)
4. **Deploy**. Note the URL, e.g. `https://aethermind.vercel.app`.

### 3. Open CORS back to the frontend
1. Render → backend service → **Environment** → set
   `CORS_ORIGINS = https://aethermind.vercel.app` (your exact Vercel URL).
2. Save → the backend redeploys. Now the browser can call the API.

### 4. Keep it warm (kills cold starts, free)
1. Create a free **UptimeRobot** (or cron-job.org) account.
2. New monitor → HTTP(s) → URL `https://<api>.onrender.com/health` → interval **10 minutes** (5 on UptimeRobot).
3. Done — the backend now stays awake.

### 5. Demo
Share the **Vercel URL**. Upload a PDF/PNG/JPEG (samples in `uploads/`, or make one
from `docs/sample-invoice.txt`), watch the Agent Activity panel, review, approve/
reject, export CSV.

## Environment variables

| Var | Where | Required | Default / example |
|-----|-------|----------|-------------------|
| `CORS_ORIGINS` | Render (backend) | For production | comma-separated origins, e.g. `https://aethermind.vercel.app` |
| `NEXT_PUBLIC_API_URL` | Vercel (frontend) | Yes | `https://aethermind-backend.onrender.com` |
| `DATABASE_URL` | Render (backend) | **Yes, in production** | Neon Postgres, psycopg v3 scheme: `postgresql+psycopg://user:password@host/db`. Local dev defaults to SQLite (`sqlite:///./database/document_intelligence.db`) if unset — dev-only, not for production. |
| `JWT_SECRET` | Render (backend) | **Yes, in production** | strong random secret (e.g. 32 random bytes, base64-encoded); boot fails fast if unset/default while `DATABASE_URL` is non-sqlite (see Phase 1 below) |
| `STORAGE_BACKEND` | Render (backend) | For production | `local` (default, ephemeral) or `s3` for durable storage via Cloudflare R2 |
| `R2_ENDPOINT` / `R2_BUCKET` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | Render (backend) | Required if `STORAGE_BACKEND=s3` | Cloudflare R2 bucket credentials — see Phase 1 below |
| `OPENAI_API_KEY` | Render (backend) | No | blank → deterministic fallback extraction |
| `OPENAI_MODEL` | Render (backend) | No | `gpt-4o` (set in `render.yaml`; use `gpt-4o-mini` to cut cost) |
| `PYTHON_VERSION` | Render (backend) | No | `3.12.8` (set in `render.yaml`) |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Render (backend) | For production | seeds the bootstrap admin user on first startup — see Phase 1 below |
| `UPLOAD_DIR` / `EXPORT_DIR` | Render (backend) | No | local filesystem paths, only relevant when `STORAGE_BACKEND=local` |

## Caveats on the free tier

- **SQLite/local storage is dev-only.** Without `DATABASE_URL` and
  `STORAGE_BACKEND=s3` set, the backend falls back to SQLite + local files. On
  Render free (no persistent disk) that data resets on every redeploy or
  recycle — fine for a quick throwaway demo, but not for a real deployment.
  For a persistent deployment, follow **Phase 1** below (Postgres + R2 + JWT
  auth) — that is the supported production setup, not an optional upgrade.
- **First deploy build** installs PyMuPDF/Pillow — takes a few minutes; subsequent
  deploys are faster.

## Phase 1 — Production persistence & identity

Upgrade from ephemeral SQLite + local file storage to Postgres + S3-compatible object storage (Cloudflare R2), with JWT auth and role-based access control.

### 1. Provision Neon Postgres

1. **Create account** at [Neon](https://neon.tech).
2. **New project** → note the connection string, e.g.:
   ```
   postgres://user:password@ep-xxx.us-east-1.neon.tech/dbname
   ```
3. Convert to psycopg v3 URL scheme (required; v2 will fail):
   ```
   DATABASE_URL=postgresql+psycopg://user:password@ep-xxx.us-east-1.neon.tech/dbname
   ```
4. Set in Render (backend) environment variables.

### 2. Provision Cloudflare R2 (S3-compatible object storage)

1. **Cloudflare dashboard** → R2 → **Create bucket** named `aethermind`.
2. **Create API token** (R2 API) for this bucket; note:
   - **Access Key ID** (`R2_ACCESS_KEY_ID`)
   - **Secret Access Key** (`R2_SECRET_ACCESS_KEY`)
   - **Endpoint URL** (`R2_ENDPOINT`), e.g. `https://abc123.r2.cloudflarestorage.com`
3. Set in Render environment:
   ```
   STORAGE_BACKEND=s3
   R2_ENDPOINT=https://abc123.r2.cloudflarestorage.com
   R2_BUCKET=aethermind
   R2_ACCESS_KEY_ID=<token>
   R2_SECRET_ACCESS_KEY=<secret>
   ```

### 3. Set JWT secret & bootstrap admin

1. Generate a strong `JWT_SECRET` (e.g. 32 random bytes, base64-encoded).
2. Set in Render:
   ```
   JWT_SECRET=<your-secret>
   JWT_EXPIRE_HOURS=12
   ADMIN_EMAIL=you@example.com
   ADMIN_PASSWORD=<temp-password>
   ```
3. On first backend startup, the system seeds one admin user with these credentials (idempotent — only creates if the `users` table is empty).

### 4. Database migrations

Render's `render.yaml` is configured to run `alembic upgrade head` on every deploy. Fresh deployments automatically apply all pending migrations; no manual action needed.

### 5. Deprecation

The old `DEMO_ACCESS_TOKEN` gate has been removed. All endpoints now require JWT auth (except `/health` and `/docs`). Use the admin bootstrap credentials to log in and manage users and roles.

## Phase 2a — Parallel pipeline

No new infrastructure required. The real multi-agent extraction pipeline is live:

- **Classifier agent** auto-detects document type; falls back to a hint if provided.
- **Per-page parallel extractors** fan out via the existing `ai_extract` call, with reconciliation/merge to unify results.
- **Validator + anomaly agent** checks schema validity and flags suspicious values for review.
- **Real agent panel** in the UI shows live extraction steps and anomalies.

**Database migration:** Migration `0003` runs automatically via the existing `alembic upgrade head` on every deploy (set in `render.yaml`). No manual action needed.

**Optional env vars** (in `.env.example`):
- `CLASSIFIER_MODEL` — LLM for doc-type classification (default: `gpt-4o-mini`).
- `PIPELINE_CONCURRENCY` — max parallel extractor calls (default: `5`).
- `PIPELINE_STAGE_TIMEOUT` — timeout per pipeline stage in seconds (default: `60`).

**Cost note:** Per-document processing now makes one classifier call + one extractor call per page, compared to the old single call. OpenAI usage increases slightly but remains within the fallback-mode estimate. Async queue + workers (Phase 2b) and the multi-document splitter remain future work.

## Phase 2b — Async processing

No new infrastructure, environment variables, or database schema changes required.

- **Async dispatch:** `POST /process` now returns `202 Accepted` immediately; the pipeline runs in a FastAPI background task.
- **Frontend polling:** The UI polls `GET /document/{id}` to retrieve status and results until a terminal state is reached.
- **Stuck-job recovery:** On backend startup, any documents stranded in the `processing` state are automatically reset to `error` and become retryable via the Retry button.

**Future work:** Durable queue (Upstash QStash) and structured logging backend remain planned for subsequent phases.

## Local development is unchanged

Defaults still target localhost, so nothing about local dev changes:
```bash
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev
```
`CORS_ORIGINS` defaults to `http://localhost:3000,http://127.0.0.1:3000`.
