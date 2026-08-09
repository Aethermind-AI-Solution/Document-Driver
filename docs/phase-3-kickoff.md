# Phase 3 — Kickoff notes (brainstorm this next)

**Status:** Not started. Prep notes so we can go brainstorm → spec → plan → build quickly. Nothing decided — the questions below are for the next session.
**Background research:** `docs/research/2026-07-29-mcp-multiagent.md` (MCP 2026 spec: stateless Streamable HTTP, routable headers, Tasks primitive; connector ecosystem; how it maps to Aethermind).

## Goal
Connect Aethermind to the outside world via MCP — either **ingesting** documents from external systems (Drive/Gmail/email) and **pushing** results out (ERP/webhook), and/or **exposing** Aethermind's extraction as MCP tools other agents can call.

## Why now
Phases 0–2b made the core real (auth, persistence, real async pipeline). The next leverage is integration: stop hand-uploading and stop copy-pasting results out — and/or let a client's own agent call Aethermind directly.

## ⚠️ Decompose first — Phase 3 is several independent slices
Do NOT try to build all of these in one spec. Pick ONE first:

| Slice | What | External auth/hosting? | $0 fit |
|-------|------|------------------------|--------|
| **S1 — Aethermind as an MCP *server*** | Expose `extract_document` / `get_confidence` / `submit_correction` as MCP tools over the 2026 stateless Streamable-HTTP transport, on our existing FastAPI | **None** — self-contained | ✅ strong |
| **S2 — MCP-client ingestion** | Backend pulls docs from Google Drive / Gmail via their MCP servers → `/upload` path | **Google OAuth app + token storage** | ⚠️ OAuth complexity |
| **S3 — Result push** | On approve, push structured output to a webhook / ERP (QuickBooks etc.) | webhook: none; ERP: OAuth | webhook ✅ / ERP ⚠️ |
| **S4 — Enrichment** | Vendor / GSTIN validation, web-search cross-checks as pipeline steps | per-provider keys | varies |

**Leaning (to confirm at brainstorm):** the kickoff for Phase 2 originally suggested the ingestion spike (S2) "start here," but given the $0 / no-card / no-external-OAuth reality we kept hitting, **S1 (expose Aethermind as an MCP server) is likely the better first slice** — it's self-contained, needs no third-party OAuth, is the most strategically differentiated ("others' agents call us"), and the new stateless HTTP transport maps cleanly onto our FastAPI. S3-webhook is the other low-friction option. S2/ERP bring real OAuth work — probably later.

## Open questions for the brainstorm (one at a time tomorrow)
1. Which slice first — **S1 (MCP server)**, S3-webhook (result push), or S2 (Drive/Gmail ingestion)?
2. If S1: which tools to expose, and the auth model for external callers (our JWT? a per-client API token? MCP's auth story)? How does an external agent authenticate as a *user/role*?
3. If S1: transport — implement the 2026 stateless Streamable-HTTP MCP server by hand, or use an MCP server SDK/library (and is one $0 + FastAPI-friendly)?
4. Prompt-injection surface: tool calls over untrusted document content — reuse/extend the grounding + injection defenses; tools return data, not instructions.
5. Scope: one slice end-to-end, or slice + a thin demo of a second?

## Where things stand (context for a fresh session)
- Phases **0, 1, 2a, 2b shipped and live in prod** (Render + Neon + Supabase + Vercel). Backend 105 / frontend 26 tests green.
- Real auth/RBAC, persistence, object storage, async multi-agent pipeline (classifier → per-page fan-out → reconciler → validator), grounding, anomaly detection.
- Roadmap: `docs/roadmap.md`. Deploy + gotchas: `docs/deployment.md` and the `aethermind-prod-deployment` memory. MCP research: `docs/research/2026-07-29-mcp-multiagent.md`.
- Independent follow-ups still open (not Phase 3): rate-limit `/auth/login`; retry() has no in-flight spinner; `/process` 409 check-then-set TOCTOU (harmless — idempotent).
