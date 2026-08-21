# A5 — Observability (lite) Design

**Date:** 2026-08-21
**Status:** Approved design (pre-plan)
**Roadmap item:** Slice **A5** (final A-track slice) — the "lite" cut of **B15** (observability). The full B15 (external metrics/alerting stack) remains a later `M`.

## Goal

The business KPIs already ship (`/documents/stats` + 8 dashboard tiles). The remaining gaps are **operational**: prod logs on Render are ad-hoc and hard to query, and there is no visibility into system health — error-status documents, documents stuck in `processing`, or per-pipeline-stage latency (the data already lives in `pipeline_trace`). A5 adds **structured JSON logging** (the roadmap's named B15 foundation) and a small **admin System-Health view** backed by an ops-metrics endpoint, all computed from existing data with no new infrastructure ($0).

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Logging | **stdlib** JSON `logging.Formatter` (no new dependency); `configure_logging()` at startup; `LOG_FORMAT=json\|plain` (default `json`) |
| Correlation fields | `document_id`, `stage`, `latency_ms`, `actor` added at high-value points (pipeline run/finish/fail, auto-approve, webhook delivery) |
| Metrics endpoint | `GET /admin/metrics` — **admin-only**; reads existing data; no schema change |
| Metrics content | `status_counts`, `errors_recent`, `stuck_processing`, `stage_latency` (p50/p95 per stage) |
| Stuck threshold | `STUCK_PROCESSING_MINUTES` (default 15) |
| Latency window | most recent `METRICS_RECENT_N` processed docs (default 200) — bounds the computation |
| Frontend | admin **System Health** screen + nav link; visual flag banner for stuck/high-error |
| Alerting | **visual flags only** — no external email/Slack (deferred to full B15) |

## Non-goals (this slice)

- External alerting (email/Slack/PagerDuty); Prometheus/Grafana or a machine-scrape `/metrics` endpoint.
- Time-series history / sparklines / charts (business KPI trends).
- Distributed tracing, log shipping/aggregation, request-ID middleware across every endpoint.
- Any schema/migration change; any change to the existing `/documents/stats` or business tiles.
- Auto-recovery of stuck docs (surfacing only; the existing startup `reset_stuck_processing` is unchanged).

---

## Structured logging

New `backend/app/logging_config.py`:
- `class JsonFormatter(logging.Formatter)` — `format(record)` returns a one-line JSON object: `{"ts": <iso8601 UTC>, "level": record.levelname, "logger": record.name, "msg": record.getMessage()}` plus any structured extras attached to the record (see below), plus `"exc"` (formatted traceback string) when `record.exc_info` is set.
- Structured extras: callers pass `logger.info("...", extra={"document_id": ..., "stage": ..., "latency_ms": ..., "actor": ...})`; the formatter copies a fixed allow-list of these keys into the JSON when present (avoids dumping all of `record.__dict__`).
- `def configure_logging() -> None` — reads `config.LOG_FORMAT`; installs a `StreamHandler` on the `"aethermind"` logger (and root, so uvicorn/app logs are consistent) with `JsonFormatter` when `json`, else a plain human formatter; idempotent (safe to call once at startup; clears prior handlers it added). Respects an existing level (default INFO).
- Called once in `backend/app/main.py` at import/startup (near app creation).

**High-value structured log points** (add `extra=...`; keep minimal):
- `agents/pipeline.py`: on pipeline finish — `logger.info("pipeline complete", extra={"document_id", "stage": "pipeline", "latency_ms": processing_time*1000, "status"})`; on failure — `logger.error("pipeline failed", extra={"document_id", "stage": <failed stage or "pipeline">}, exc_info=True)` (or reuse the existing except-branch log with extras). Auto-approve decision in `_maybe_auto_approve` — `logger.info("auto-approved", extra={"document_id", "actor": "system:auto-approve"})`.
- `webhooks.py`: delivery outcome — `logger.info("webhook delivered"...)` / `logger.warning("webhook failed"...)` with `extra={"document_id", "latency_ms"?}`.

Existing `logging.getLogger("aethermind")` / `_log.exception(...)` calls keep working and are now JSON-formatted.

## Ops-metrics endpoint

`GET /admin/metrics` (`Depends(auth.require_role("admin"))`), returns:
```jsonc
{
  "status_counts": { "uploaded": n, "processing": n, "processed": n, "review_required": n,
                     "approved": n, "rejected": n, "reopened": n, "error": n },
  "errors_recent": [ { "id", "filename", "document_type", "detail", "timestamp" } ],   // last 10 error docs
  "stuck_processing": { "threshold_minutes": 15, "count": n,
                        "documents": [ { "id", "filename", "minutes": m } ] },
  "stage_latency": [ { "stage": "Classifier", "p50_ms": x, "p95_ms": y, "n": k }, ... ],
  "sample_size": METRICS_RECENT_N
}
```

- **status_counts:** `GROUP BY status` (or per-status counts); include every known status (0 when none).
- **errors_recent:** the most recent documents with `status=="error"`, ordered by `upload_date desc` (or by the latest audit), limited to 10; `detail` = the latest `AuditLog` row for that doc with `action=="Processing failed"` (or the doc's most recent audit detail).
- **stuck_processing:** documents with `status=="processing"` whose most recent activity is older than `STUCK_PROCESSING_MINUTES`. "Age" is derived from `upload_date` (simplest reliable timestamp; `processing_time` is null while processing). `minutes` = whole minutes since `upload_date`.
- **stage_latency:** load `pipeline_trace` for the most recent `METRICS_RECENT_N` documents that have a non-null `pipeline_trace`; for each stage name across those traces, collect `duration_ms` values and compute p50/p95 (nearest-rank percentile, pure-Python; `None`/omit stages with n==0). Bounded by `METRICS_RECENT_N`.

A small helper `def _percentile(values: list[int], p: float) -> int` (nearest-rank) lives in `main.py` or a util; unit-tested.

`GET /health` is unchanged (pure liveness for the keep-warm ping).

## Frontend — System Health screen

`frontend/app/health/page.tsx` (admin-only, `me()`-guarded like the other admin screens; "Health" nav link in `page.tsx`, same guard/className as Users/Webhooks/Suggested Schemas/Auto-approve):
- **Flag banner** at top: red when `stuck_processing.count > 0`; amber when `errors_recent` is non-empty; green "All systems nominal" otherwise.
- **Status breakdown:** the `status_counts` as labeled chips/cells (reuse the queue badge colors where natural).
- **Stuck processing:** count + list (id, filename, minutes) — only shown when count > 0.
- **Recent errors:** list of `{filename, document_type, detail, timestamp}`.
- **Stage latency:** a small table — stage · p50 ms · p95 ms · n.
- Reuses the `api()` client; polls once on load (a "Refresh" button; no auto-polling for lite).

## Config

Additions (all defaulted, in `backend/app/config.py`):
- `LOG_FORMAT = os.getenv("LOG_FORMAT", "json")`
- `STUCK_PROCESSING_MINUTES = int(os.getenv("STUCK_PROCESSING_MINUTES", "15"))`
- `METRICS_RECENT_N = int(os.getenv("METRICS_RECENT_N", "200"))`

## Error handling

- `configure_logging()` is idempotent and must never raise at startup (guard around handler setup).
- `/admin/metrics` computes each section independently; a malformed `pipeline_trace` on one doc is skipped (per-doc try/except in the latency aggregation) rather than failing the whole endpoint.
- Empty system (no docs) → zero counts, empty lists, empty `stage_latency`, `stuck_processing.count == 0` — no division errors.

## Testing (offline)

**Backend:**
- `JsonFormatter`: a record formats to valid JSON with `ts/level/logger/msg`; structured `extra` allow-list keys appear when passed; `exc_info` produces an `exc` field; `configure_logging()` is idempotent (calling twice doesn't duplicate handlers) and honors `LOG_FORMAT=plain`.
- `_percentile`: known inputs → expected nearest-rank p50/p95; single-element and empty handling.
- `GET /admin/metrics`: admin-only (reviewer → 403); `status_counts` reflects seeded docs across statuses; `errors_recent` returns error docs with their "Processing failed" detail; `stuck_processing` flags a `processing` doc with an old `upload_date` and ignores a recent one (respecting `STUCK_PROCESSING_MINUTES`, monkeypatched small); `stage_latency` computes p50/p95 from seeded `pipeline_trace` and is bounded to `METRICS_RECENT_N`; empty DB → zeros/empties without error.

**Frontend (vitest, jsdom):**
- System Health screen renders the status breakdown, a stuck-processing banner when `count>0`, the recent-errors list, and the stage-latency table from a mocked `/admin/metrics`; non-admin path handled like the other admin screens.
- Keep backend (241) / frontend (39) green, net new on top.

## Build sequencing (one plan, ordered tasks)

1. `backend/app/logging_config.py` (`JsonFormatter` + `configure_logging`) + config flags + wire `configure_logging()` into `main.py` startup, with tests.
2. Structured `extra` correlation fields at the high-value log points (pipeline, auto-approve, webhook), with a light test that a log record carries the fields.
3. `GET /admin/metrics` (+ `_percentile` helper) — status_counts / errors_recent / stuck_processing / stage_latency, with tests.
4. Frontend System Health screen + nav link + tests.
5. Docs: `.env.example` (the 3 new vars), `docs/deployment.md` (structured logging + `/admin/metrics` + the System Health screen), `docs/roadmap.md` (mark A5 shipped — A-track complete; full B15 alerting/metrics-stack remains).

## Deployment notes

No migration, no new dependencies. New optional env: `LOG_FORMAT` (default `json`), `STUCK_PROCESSING_MINUTES` (15), `METRICS_RECENT_N` (200). On Render, JSON logs become the default (set `LOG_FORMAT=plain` for local human-readable logs if preferred). The System Health screen and `/admin/metrics` are admin-only. Nothing changes for non-admin users or the processing pipeline's behavior.
