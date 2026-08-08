# Phase 2a — Real Multi-Agent Parallel Pipeline Design

**Date:** 2026-08-08
**Status:** Approved design (pre-plan)
**Roadmap item:** Phase 2 (partial) — the real agent pipeline. Explicitly the **2a** slice; async queue/workers (2b), multi-document splitter, and per-field-group fan-out are separate later cycles.

## Goal

Replace the single synchronous `ai_extract` call and the cosmetic "6 agents" UI with a real multi-agent pipeline — classifier → per-page extractor fan-out → reconciliation → validator/anomaly → human review — orchestrated in plain-Python `asyncio`, with a persisted, real agent trace surfaced in the UI.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Orchestration | Internal `asyncio` (no framework); agents behind a uniform async interface |
| Parallelism | Per-page extractor fan-out (`asyncio.gather` + `Semaphore`) + reconciliation merge |
| Validator | Deterministic: schema rules (`passes_validation`) + arithmetic (Σ line-items ≈ total, tax) + duplicate detection |
| Classifier | Auto-detects `document_type`; the upload-time selection is a hint/fallback; mismatch logged |
| Agent trace | `Document.pipeline_trace` JSON column (not a separate table) |
| Execution | `/process` becomes `async`, awaits the pipeline in-request (no queue this cycle) |
| Extractor reuse | Per-page extractor reuses existing `ai_extract` (preserves OpenAI/Gemini/regex fallback + grounding) |

## Non-goals (this cycle)

- Async queue + workers, status polling, structured-logging backend (**2b**).
- Multi-*document* splitting (a bundle of separate docs) — the **splitter** slice.
- Per-field-group fan-out; LLM anomaly pass; batch upload (Phase 5).
- Changing the extraction provider or existing model values (out of scope).

---

## Architecture

New package `backend/app/agents/`:
- `base.py` — `PipelineContext`, `StageResult`, and the `Agent` protocol:
  ```python
  class Agent(Protocol):
      name: str
      async def run(self, ctx: PipelineContext) -> StageResult
  ```
- `classifier.py`, `extractor.py`, `reconciler.py`, `validator.py` — one agent each.
- `pipeline.py` — `async def run_pipeline(db, document, hint_type) -> Document`; a thin runner that executes stages in order, times each, and records a trace.

`PipelineContext` (a dataclass) carries: `db`, `document`, `pages` (list of `{index, pdf_bytes, text}`), `schema`, `fields` (accumulated), `hint_type`, and a mutable `trace` list. Each agent reads/writes `ctx` and returns a `StageResult(name, status, detail, duration_ms)` appended to `ctx.trace`.

**Concurrency:** the extractor stage runs pages with `asyncio.gather` under an `asyncio.Semaphore(config.PIPELINE_CONCURRENCY)` (default 5) to bound simultaneous LLM calls.

## Stages

### 1. Classifier agent (`classifier.py`)
- Input: the document (first page bytes/text is sufficient) + the list of available schema keys/names (`available_schemas(db)`).
- Calls a cheap LLM (`config.CLASSIFIER_MODEL`) to return one schema `key` + a confidence.
- Output: sets `ctx.schema` and `document.document_type`. If the result disagrees with `hint_type`, `log(... "Classified", detail="hint=X chosen=Y")`. On LLM failure or low confidence → fall back to `hint_type` if valid, else `"invoice"`.
- Trace: `{name:"Classifier", status, detail:"invoice (0.97)"}`.

### 2. Per-page extractor fan-out (`extractor.py`)
- Split the stored PDF into single-page PDFs with PyMuPDF (`fitz`): for each page `i`, `doc.select([i])` → page bytes; `page.get_text()` (cleaned via existing `_clean_text`) → page text. Images (`.png/.jpg`) are a single page.
- For each page, concurrently call the **existing** `ai_extract(page_text, ctx.schema["fields"], page_pdf_path)` (written to a temp file, cleaned up) — reusing provider dispatch, regex fallback, and grounding (values grounded against that page's text). Wrap the sync `ai_extract` via `asyncio.to_thread` so fan-out is real without rewriting it.
- Output: `ctx.page_results` = list (per page) of the grounded field lists `ai_extract` returns.
- Trace: `{name:"Extractor", detail:"3 pages"}`.

### 3. Reconciliation (`reconciler.py`, deterministic)
- Merge `ctx.page_results` into one field list keyed by `field_name`:
  - **Array fields with columns** (e.g. `line_items`): concatenate rows across pages in page order.
  - **Scalars:** choose the best entry by grounding rank `grounded(0.95/0.90) > unverified > ungrounded > absent`; among equal rank, first non-null in page order. Carry that entry's `field_value/source_quote/grounded/confidence`.
- Output: `ctx.fields` = the reconciled field list (same shape `ai_extract` produces, so downstream persistence is unchanged).
- Trace: `{name:"Reconciler", detail:"12 fields from 3 pages"}`.

### 4. Validator / anomaly agent (`validator.py`, deterministic)
- **Schema rules:** existing `passes_validation(field_def, value)` → per-field `validated`.
- **Arithmetic:** if `line_items` present with numeric `amount` and a `total` field exists — parse numbers (strip ₹/commas, reuse cleaning), compare `Σ amounts` to `total` within a small tolerance; on mismatch add an anomaly. If `tax`/`subtotal` present, check `subtotal + tax ≈ total`.
- **Duplicate detection:** query the DB for an existing *other* document whose extracted `invoice_number` + `seller_gstin` match this one → anomaly `"Possible duplicate of #<id>"`.
- Output: per-field `validated` flags + `ctx.anomalies` (list of strings). Anomalies force `review_required = True` and are logged + surfaced.
- Trace: `{name:"Validator", detail:"ok" | "2 anomalies"}`.

### 5. Review (existing HITL)
- `document.confidence` = mean field confidence (as today). `review_required = any(conf < 0.9 or not validated) or bool(anomalies)`. `status = "review_required" | "processed"`. Anomalies stored (see persistence).

## Persistence & serialization

- **Migration 3** adds `Document.pipeline_trace: JSON | None` (list of stage dicts) and `Document.anomalies: JSON | None` (list of strings).
- `process_document` persists reconciled fields exactly as today (`ExtractedField` incl. `original_value` from Phase 0), plus `document.pipeline_trace` and `document.anomalies`.
- `serialize()` returns `pipeline_trace` and `anomalies`.

## Frontend

- The Agent Activity panel renders `document.pipeline_trace` (real stage names, statuses, `duration_ms`) instead of `deriveAgentTimeline`'s derived labels. Keep `deriveAgentTimeline` only as a fallback when `pipeline_trace` is absent (older docs).
- Surface `anomalies` in the review panel as warning chips.
- Upload UI: the document-type selector becomes optional ("Auto-detect") since the classifier decides; a chosen value is sent as the hint.

## Execution & config

- `/process/{id}` becomes `async def` and `await run_pipeline(...)`. Rate-limit + `require_role("admin","reviewer")` unchanged.
- Config additions: `CLASSIFIER_MODEL` (default a cheap model), `PIPELINE_CONCURRENCY` (default 5), `PIPELINE_STAGE_TIMEOUT` seconds (default 60).
- Uses `AsyncOpenAI` for the classifier; the extractor reuses sync `ai_extract` via `asyncio.to_thread`.

## Error handling

- Extractor page failure → that page contributes an all-absent result; pipeline continues; trace marks the page. If *all* pages fail extraction → the existing regex fallback still applies per page (via `ai_extract`), so output is degraded not empty.
- Classifier failure/low-confidence → hint/`invoice` fallback (never blocks).
- Reconciler/validator are pure code (robust).
- Any unhandled pipeline exception → `document.status = "error"`, logged (existing behavior preserved).
- Per-stage `asyncio.wait_for(..., PIPELINE_STAGE_TIMEOUT)`.

## Testing (all offline — no real LLM/network)

- Agents accept injectable implementations (or monkeypatch the LLM call) so tests run offline.
- **Classifier:** returns a schema key; disagreement with hint is logged; failure → hint fallback.
- **Extractor fan-out:** given a 3-page doc and a stubbed `ai_extract`, all pages are processed and `page_results` has 3 entries; a raised error on one page → that page all-absent, others intact.
- **Reconciler:** scalars pick best-grounded/first-non-null; `line_items` concatenate in page order.
- **Validator:** arithmetic mismatch (Σ amounts ≠ total) flags an anomaly; matching sums pass; duplicate `invoice_number`+`seller_gstin` in DB flags a duplicate anomaly; unique doc passes.
- **Trace:** `pipeline_trace` persisted with a stage entry per agent, each with `duration_ms`.
- **End-to-end:** `run_pipeline` on a stubbed 1-page invoice populates fields, trace, anomalies, status.
- **Migration:** `0003` upgrade adds `pipeline_trace`/`anomalies`; chains from `0002`; reversible (SQLite batch mode).
- Existing backend + frontend suites stay green (extractor reuses `ai_extract`, so extraction/grounding tests are unaffected).
- Frontend: panel renders a real trace; anomaly chips appear when `anomalies` non-empty.

## Build sequencing (one plan, ordered tasks)

1. `agents/base.py` (context/protocol/result) + config additions.
2. Classifier agent (+ tests).
3. Page-splitting util + extractor fan-out agent (+ tests).
4. Reconciler (+ tests).
5. Validator/anomaly (+ tests).
6. Migration 3 (`pipeline_trace`, `anomalies`) + model/serialize changes.
7. `pipeline.py` runner wiring all stages + `process_document`/`/process` async integration (+ end-to-end test).
8. Frontend: real trace panel + anomaly chips + auto-detect upload option.
9. Docs + roadmap update.

## Deployment notes

- No new infrastructure. New env (optional, defaulted): `CLASSIFIER_MODEL`, `PIPELINE_CONCURRENCY`, `PIPELINE_STAGE_TIMEOUT`. Add to `.env.example`/`render.yaml`. Migration runs via the existing `alembic upgrade head` on deploy.
