# Aethermind — Phased Roadmap

**Date:** 2026-07-29 · Sequences everything in `docs/TODO.md` + `docs/research/2026-07-29-mcp-multiagent.md` into dependency-ordered phases. Planning only — no build until picked.
Effort: XS (<½d) · S (≤1d) · M (1–3d) · L (>3d).

**Ordering logic:** harden what exists → make data/identity real → make the agents real & parallel → connect to the world → get adaptive → automate the lifecycle → enterprise-harden. Each phase depends on the ones above it.

---

## Phase 0 — Harden the foundation (debt & quick wins) ✅ DONE (2026-08-07, `feat/phase-0-quick-wins`)
*Goal: fix correctness bugs and unblock later phases. Do first — cheap, high-leverage.*
- ✅ **Q1** SQLite `WAL` + `busy_timeout` — removes "database is locked" risk
- ✅ **Q2** Store **original AI value** separately from human-corrected — **unblocks the learning loop (Phase 4)**
- ✅ **Q5** `datetime.utcnow()` → tz-aware + validate `POST /schemas` payload
- ✅ **Q3** Grounding: substring → word-boundary match
- ✅ **Q4** CSV export: flatten `line_items` rows into a columnar section
- ✅ **Q6** Titleize `gstin`; guard all-empty-cell grounding
*(Backend 54 tests, frontend 13 tests passing.)*

## Phase 1 — Persistence & identity (make it real) ✅ DONE (2026-08-08, `feat/phase-1-persistence-identity`)
*Goal: data survives; every action has an owner. Prerequisite for connectors, audit, learning.*
- ✅ **B6** Postgres + object storage (R2/S3) — replace ephemeral SQLite/local files
- ✅ **B5** Auth + roles (RBAC) + **actor-stamped audit log**

## Phase 2 — Real multi-agent parallel pipeline (make the agents real)
*Goal: replace the single sync OpenAI call + cosmetic "6 agents" with a real, parallel pipeline. This is the MCP multi-agent + parallel-processing core. Supersedes Q7.*

### Phase 2a ✅ Shipped (2026-08-08)
- ✅ **B8** Classifier agent (AI picks doc type) `M`
- ✅ **Parallel fan-out extractors** (per field-group / per page) + reconciliation/merge agent `L`
- ✅ Validator / anomaly agent `M`
- ✅ Real agent panel (live extraction steps + anomaly chips in UI) `M`
- *Grounding + HITL review already exist — wired into this pipeline.*

### Phase 2b ✅ Shipped (2026-08-09)
- ✅ **Async processing** (background tasks, frontend polling, startup recovery) `L`

### Phase 2 — Future items
- Splitter (multi-doc / multi-page) `M`
- Durable queue (Upstash QStash) `L`
- Structured logging (foundation for B15) `S`

## Phase 3 — MCP connectors & integration (connect to the world)
*Goal: stop hand-building integrations; ingest and push via the MCP ecosystem. Needs Phase 1 persistence.*

### Phase 3 (S1) ✅ Shipped (2026-08-10)
- ✅ **Aethermind as an MCP *server*** — expose `list_document_types`, `extract_document`, `get_document`, `submit_correction` as tools `M` *(strategic: others' agents call us)*

### Phase 3 — Future items
- **S2** **MCP-client ingestion spike** — pull a doc from Google Drive / Gmail via its MCP server into `/upload` `M` *(smallest slice — start here)*
- **S3** Email-inbox ingestion (part of **B10**) `M`
- **S4** ERP / accounting / webhook push (part of **B10**) `L`
- **S5** Enrichment connectors (vendor/GSTIN validation, web search) `M`

## Phase 4 — Adaptivity & learning (the moat)
*Goal: adapt to any document and get better over time. Needs Q2 + real pipeline.*
- ✅ **B14** Learning loop from reviewer corrections (learns from human corrections on approved documents, injects hints into extraction)
- **F4** Dynamic / AI-suggested schemas (Schema-Author agent) `L` — the real "fields aren't hardcoded" fix
- **B4+** Deeper confidence — logprobs / self-consistency / bounding boxes `M–L`

## Phase 5 — Straight-through processing & review lifecycle
*Goal: automate the easy cases, complete the human workflow. Needs validator + confidence.*
- **B9** Auto-approve high-confidence (straight-through processing) `M`
- **B11** Reopen/rework + re-process after edits `M`
- **B12** Batch/bulk upload + pagination + delete/archive `M`

## Phase 6 — Enterprise readiness
*Goal: unlock regulated buyers and operate at scale.*
- **B13** Compliance layer — encryption at rest, retention, PII redaction `L`
- **B15** Observability — metrics + alerting + dashboards `M`
- OCR path for scanned/image-only docs `M`
- SSO + audit-trail hardening (rides on MCP 2026 enterprise direction) `M`
- Richer anomaly / duplicate-invoice detection `M`

---

## Fast-path (if we want a client-ready leap, minimum phases)
Phase 0 (Q1, Q2) → Phase 1 (B6) → Phase 3 spike (Drive ingestion + expose one MCP tool) → Phase 2 (classifier + parallel extract).
That yields: persistent, connector-fed, real-agent extraction — the demo story that matches the proposal.
