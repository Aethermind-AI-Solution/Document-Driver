# Aethermind — Phased Roadmap

**Date:** 2026-07-29 · Sequences everything in `docs/TODO.md` + `docs/research/2026-07-29-mcp-multiagent.md` into dependency-ordered phases. Planning only — no build until picked.
Effort: XS (<½d) · S (≤1d) · M (1–3d) · L (>3d).

**Ordering logic:** harden what exists → make data/identity real → make the agents real & parallel → connect to the world → get adaptive → automate the lifecycle → enterprise-harden. Each phase depends on the ones above it.

> **Revised execution order (2026-08-15 council review — see [`docs/council/2026-08-15-next-phase-council-review.md`](council/2026-08-15-next-phase-council-review.md)):** the council reprioritized the *next* work across the phases below. Recommended sequence:
> **A1** ✅ Shipped (2026-08-15, `feat/p0-gui-sweep`) — frontend queue search/filter/pagination (fixes the 6-doc display cap) + audit-trail render + confirm dialogs → **A2** ✅ Shipped (2026-08-16, `feat/b11-reopen-rework`) — B11 reopen/rework: explicit status state machine (`TRANSITIONS`/`can_transition`), reopen action (approved = admin-only + reason; fields preserved), `/process` blocked on terminal/reopened, `revision` counter in the webhook payload, status-gated Review panel → **A3** ✅ Shipped (2026-08-16, `feat/a3-confidence-eval`) — `document.confidence` = min over required fields (a wrong required field can't be averaged away); offline eval harness `scripts/eval.py` (per-field accuracy, confidence-bucket reliability, grounded-but-wrong rate; correction-derived = honest **upper bound**); `/documents/stats.field_agreement_rate` + "Fields accepted as-is" tile; golden-set loader ships (curating the data is a **B9 prerequisite**) → **A4** (next) B9 auto-approve/STP (gated: per-type opt-in default-off, calibrated floor via `services.document_confidence`, machine-vs-human audit, kill switch) → **A5** B15 observability (lite).
> In parallel: add an `org_id`/tenant column now (cheap while the schema is small); tenant isolation + retention (B13) gate any multi-customer sale. OCR stays deferred (scanned docs already route safely to human review). **Do not ship B9 on the current uncalibrated confidence heuristic.**

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

### Phase 3 (S3) ✅ Shipped (2026-08-11)
- ✅ **Webhook push on approve** (part of **B10**) — on approve, POST the structured result to a per-document-type webhook, admin-configured via the Webhooks screen/API, with optional HMAC signing and async single-attempt delivery `M`

### Phase 3 — Future items
- **S2** **MCP-client ingestion spike** — pull a doc from Google Drive / Gmail via its MCP server into `/upload` `M` *(smallest slice — start here)*
- **S4** Enrichment connectors (vendor/GSTIN validation, web search; ERP-specific adapters remain future) `M`
- **S5** Email-inbox ingestion (part of **B10**) `M`

## Phase 4 — Adaptivity & learning (the moat)
*Goal: adapt to any document and get better over time. Needs Q2 + real pipeline.*
- ✅ **B14** Learning loop from reviewer corrections (learns from human corrections on approved documents, injects hints into extraction)

### Phase 4 (F4 S1) ✅ Shipped (2026-08-15)
- ✅ **F4** Dynamic / AI-suggested schemas (Schema-Author agent) `L` — the real "fields aren't hardcoded" fix. When the classifier can't confidently match a document to any approved schema, a Schema-Author LLM auto-proposes a schema and the document is extracted against it immediately; the proposal is stored as a **suggested** draft (not yet usable by the classifier) until an admin approves, edits, or rejects it via the Suggested Schemas admin screen / `/schemas` review API — a human-gated but reusable schema catalog.

### Phase 4 — Future items
- On-demand "suggest schema from this doc" action (propose without waiting for a low-confidence classification)
- Automatic de-duplication of near-identical suggested drafts
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
