# Phase 2b — Kickoff notes (brainstorm this next)

**Status:** Not started. Prep notes so we can go brainstorm → spec → plan → build quickly. Nothing decided yet — the questions below are for the next session.

## Goal
Decouple the multi-agent pipeline from the HTTP request so `/process` returns immediately and heavy runs (classifier + per-page OpenAI calls) don't tie up a web request — plus structured logging (the B15 foundation). Today `/process` runs the whole pipeline synchronously in-request via `asyncio.run`.

## Why now
- Render free is a single web instance that **spins down after ~15 min idle** and is CPU-limited; a long synchronous `/process` can be slow and, if it ever exceeds ~100s, hits Render's proxy timeout.
- Multi-page docs → several OpenAI calls → seconds of work holding a request.

## ⚠️ The central constraint to resolve first ($0 / no card)
**Render's free tier does not include always-on background workers or cron** (those are paid — *confirm current Render pricing at brainstorm time*). So the classic "Redis queue + separate worker process" pattern isn't free here. That reframes the whole design. Candidate approaches:

| # | Approach | How it works | Pros | Cons |
|---|----------|--------------|------|------|
| A | **FastAPI BackgroundTasks** (in-process) | `/process` returns 202 immediately; the pipeline runs in a background task on the same web instance; frontend polls `/document/{id}` for status | Zero new infra, truly $0, small change | Not durable — if the instance sleeps/restarts mid-run the job is lost; still competes for the one instance's CPU |
| B | **Upstash QStash callback** (serverless queue) | `/process` enqueues to QStash (HTTP, free tier); QStash POSTs back to an internal `/process-run/{id}` endpoint with retries | Durable + retries, no always-on worker, fits Render free, no card (verify) | New dependency + a signed internal endpoint; still runs the work on the web instance when called back |
| C | **Paid Render worker + Redis (RQ/Arq)** | Proper queue + dedicated worker | Real production pattern, scalable, durable | Costs money — violates the current $0 constraint |
| D | **Defer 2b** | Keep sync `/process`; just add a spinner/timeout polish | No work | Doesn't solve the tie-up |

**Leaning:** A as the pragmatic $0 step (returns fast, poll for status), with B as the upgrade when durability matters. Decide at brainstorm.

## Open questions for the brainstorm (one at a time tomorrow)
1. Scope this cycle to **async processing only**, or bundle **structured logging** too?
2. Which approach (A / B / C / D) given $0-no-card still holds?
3. Status delivery to the UI: **polling** `/document/{id}` (simplest) vs SSE/websocket?
4. Job/status model: reuse `document.status` (`uploaded→processing→review_required/processed/error`) or add a small `jobs`/status field + progress?
5. Structured logging target: **JSON to stdout** (Render captures it, $0) vs a logging service free tier?
6. Idempotency/retries: if a job is retried (B) or re-run, ensure re-processing is safe (we already delete+recreate fields — confirm).

## Where things stand (context for a fresh session)
- Phases 0, 1, 2a are **shipped and live in prod** (Render + Neon + Supabase + Vercel), backend 99 tests / frontend 22 green.
- Pipeline: `backend/app/agents/` (classifier → per-page extractor fan-out → reconciler → validator), run via `run_pipeline`; `/process` is sync and calls it through `asyncio.run`.
- Roadmap: `docs/roadmap.md`. Deploy specifics + gotchas: `docs/deployment.md` and the `aethermind-prod-deployment` memory.
- Other logged follow-ups (independent of 2b): rate-limit `/auth/login`; clean dead imports in `services.py`.
