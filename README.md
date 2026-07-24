# Aethermind Document Intelligence Engine

An enterprise-style document operations MVP for BPO teams. It uses a configurable schema layer rather than treating invoice extraction as a one-off workflow.

## Workflow

`Document type → schema → AI extraction → validation → human review → export`

Built-in schemas include Invoice, Purchase Order, Bill of Lading, Insurance Claim, Product Catalog, Customs Declaration, and Medical Record. Create further schemas through `POST /schemas` without changing the extraction pipeline.

## Run locally

Prerequisites: Python 3.12+ and Node 22+.

```bash
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`. SQLite is created automatically at `database/document_intelligence.db`.

Alternatively, run both services with `docker compose up --build`.

## AI configuration

Copy `.env.example` to `.env`, then set `OPENAI_API_KEY` to enable the OpenAI Responses API path. `.env` is ignored by Git and must never be committed. Restart the backend after changing it. Without it, the MVP remains fully usable with deterministic text extraction for PDFs, so demos do not depend on credentials. The service separates the AI adapter (`backend/app/services.py`) from persistence and workflow logic.

## API

- `POST /upload?document_type=invoice`
- `POST /process/{id}`
- `GET /documents`, `GET /document/{id}`
- `PUT /document/{id}`
- `GET /export/{id}?format=csv|json`
- `GET/POST /schemas`

Use [`docs/sample-invoice.txt`](docs/sample-invoice.txt) as source content when creating a sample PDF for testing. Uploads accept PDF, PNG and JPEG.

## Architecture

- **Frontend:** Next.js 15 dashboard with upload, schema selection, processing feedback, confidence-aware review, approval, and CSV export.
- **Backend:** FastAPI, SQLAlchemy, SQLite, Pydantic request validation, and a repository-ready domain layout.
- **Controls:** Field-level confidence, validation state, reviewer edits, status transitions, and immutable-style audit entries.
- **Storage:** Local `uploads/`, `exports/`, and SQLite for MVP; each is isolated behind configuration for production replacement.

## Deployment

Deploy as a free, shareable demo (Next.js on Vercel + FastAPI on Render, kept
warm with a free uptime pinger). See [`docs/deployment.md`](docs/deployment.md)
for the full step-by-step. Configure with `CORS_ORIGINS` (backend) and
`NEXT_PUBLIC_API_URL` (frontend).
