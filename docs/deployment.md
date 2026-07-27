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
| `OPENAI_API_KEY` | Render (backend) | No | blank → deterministic fallback extraction |
| `OPENAI_MODEL` | Render (backend) | No | `gpt-4o` (set in `render.yaml`; use `gpt-4o-mini` to cut cost) |
| `PYTHON_VERSION` | Render (backend) | No | `3.12.8` (set in `render.yaml`) |
| `DATABASE_URL` / `UPLOAD_DIR` / `EXPORT_DIR` | Render (backend) | No | default local paths; override to point at a persistent disk later |

## Caveats on the free tier

- **Data is ephemeral.** No persistent disk on Render free → the SQLite DB and
  uploaded files reset on every redeploy (and if the service is ever recycled).
  Fine for a demo. To persist later: add a Render Disk (paid) and set
  `DATABASE_URL`/`UPLOAD_DIR`/`EXPORT_DIR` to a path on it, or move to Postgres + S3.
- **First deploy build** installs PyMuPDF/Pillow — takes a few minutes; subsequent
  deploys are faster.

## Local development is unchanged

Defaults still target localhost, so nothing about local dev changes:
```bash
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev
```
`CORS_ORIGINS` defaults to `http://localhost:3000,http://127.0.0.1:3000`.
