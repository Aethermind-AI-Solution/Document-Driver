# A4 / B9 — Auto-Approve (Straight-Through Processing) Design

**Date:** 2026-08-16
**Status:** Approved design (pre-plan). **Planning only — not yet executed.**
**Roadmap item:** Slice **A4** from the 2026-08-15 council review — B9 auto-approve / straight-through processing (STP), the automation payoff. Builds on A3 (`document_confidence` + eval harness) and B11 (reopen safety net). Informed by a four-perspective design council.

## Goal

When a freshly-processed document clears a strict, per-type gate, finalize it as `approved` with **no human in the loop** and push it to the configured webhook/ERP. This is the core automation value — but it is an unattended decision into downstream systems handling sensitive data, so it ships **default-off**, behind belt-and-suspenders guardrails, with the B11 reopen path as the live undo.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Safety model | **Belt-and-suspenders**: global env kill-switch (default OFF) + per-type opt-in (default OFF) + per-type calibrated `min_confidence` floor + machine audit stamp |
| Gate | `AUTO_APPROVE_ENABLED` **and** per-type `enabled` **and** `not review_required` (no anomalies, all fields validated & ≥0.9) **and** `document.confidence >= min_confidence` (floor **must be > 0.9**) |
| Anti-drift seams | `services.should_auto_approve(db, doc)` (reads already-computed confidence/review_required; never recomputes) + `services.apply_approval(...)` (the single "become approved" path, used by human AND machine) |
| Where it fires | Pipeline finalize, newly-processed docs only; `deliver_webhook` called directly (own session) after the approval commit |
| Config | New `AutoApproveConfig` table (per `document_type`), admin CRUD, default off |
| Kill switches | Global `AUTO_APPROVE_ENABLED` env (deploy-time belt); **per-type DB flag read fresh per document = the instant kill** |
| Machine identity | Audit action `"Auto-approved"`, `actor=None` (never the `/process` triggerer), gate values in `details`; webhook `approved_by="system:auto-approve"` |
| Webhook reliability | `deliver_webhook` gains retry-with-backoff + writes `Document.webhook_status`; `/documents/stats` surfaces an approved-but-undelivered count |
| Enable safeguard | The admin enable screen shows the type's A3 eval snapshot and **disables the toggle when `n < min_n`** ("insufficient data"); enabling is audited |

## Non-goals (this slice)

- Automatic circuit-breaker / percentage canary rollout / auto-disable on reopen-rate spike (A5).
- Trend charts, time-series accuracy, alerting, a mandatory sampling-review queue (A5).
- An `AuditLog.actor_kind` enum column (fast-follow; v1 uses the distinct action string + null actor).
- Auto-approve without a webhook push as a separate mode; retroactive auto-approval of already-processed docs.
- Code-enforcing golden-set existence (it's an in-product soft gate + documented prerequisite, per the locked model).

---

## Data model

**Migration `0007` — `AutoApproveConfig`** (mirrors `WebhookConfig`; chains from `0006`):
```python
class AutoApproveConfig(Base):
    __tablename__ = "auto_approve_configs"
    id: int PK
    document_type: str            # unique, indexed
    enabled: bool = False
    min_confidence: float
    created_at: datetime = _utcnow
```
No row for a type ⇒ auto-approve disabled for it. `document_type` is a free string (builtins have no `SchemaDefinition` row), same as `WebhookConfig`.

**Migration `0008` — webhook delivery status + auto-approve flag on `Document`**:
```python
webhook_status: str | None       # None = n/a, "pending" | "delivered" | "failed"
webhook_detail: str | None       # last delivery detail / error (Text)
auto_approved: bool = False       # sticky: this doc was auto-approved by the machine at least once
```
`webhook_status`/`webhook_detail` nullable (backfill null); `auto_approved` defaults False (`server_default="0"`). `auto_approved` is set True by the pipeline's auto-approve path (via `apply_approval`) and is **sticky** — it stays True even if the doc is later reopened/re-approved by a human, so the queue "Auto-approved" badge and the `auto_approved_reopen_rate` metric ("auto-approved docs later reopened") both have a clean, single-column signal without an audit-row query.

## Backend — shared approval seams

`services.should_auto_approve(db, document) -> bool`:
```python
def should_auto_approve(db, document) -> bool:
    if not config.AUTO_APPROVE_ENABLED:
        return False
    cfg = db.query(AutoApproveConfig).filter_by(document_type=document.document_type).first()
    if not cfg or not cfg.enabled:
        return False
    return (not document.review_required
            and document.confidence is not None
            and document.confidence >= cfg.min_confidence)
```
It **reads** `document.confidence` / `document.review_required` as already set by the pipeline — it never recomputes them (that would risk drifting from A3's `document_confidence`).

`services.apply_approval(db, document, prior_status, actor, action_label="Approved", details="") -> None`:
```python
def apply_approval(db, document, prior_status, actor, action_label="Approved", details=""):
    if not can_transition(prior_status, "approved"):
        raise ValueError(f"Cannot move a document from '{prior_status}' to 'approved'")
    document.status = "approved"
    document.review_required = False
    document.revision += 1
    log(db, document.id, action_label, details, actor=actor)
```
This is the **only** place that performs the "become approved" state change (status + revision + audit + transition check). Both callers use it:
- **`PUT /document/{id}` (human approve):** the existing approve branch is refactored to call `apply_approval(..., actor=user, action_label="Approved", details="Human review completed")`, translating `ValueError` → the existing `HTTPException(409, ...)`. Reject/save branches unchanged.
- **Pipeline (machine approve):** see below.

## Pipeline integration

In `agents/pipeline.py`, after the existing finalize block computes `document.confidence`, `document.review_required`, `document.status`, and logs `"Processed"`:
```python
auto = services.should_auto_approve(db, document)
if auto:
    services.apply_approval(db, document, prior_status=document.status, actor=None,
                            action_label="Auto-approved",
                            details=f"confidence {document.confidence:.2f} >= floor; validator-clean, no anomalies")
    document.auto_approved = True
    cfg_w = db.query(WebhookConfig).filter_by(document_type=document.document_type, active=True).first()
    if cfg_w:
        document.webhook_status = "pending"
db.commit(); db.refresh(document)
if auto and document.webhook_status == "pending":
    deliver_webhook(cfg_w.id, document.id, "system:auto-approve")   # own session; sets delivered/failed
return document
```
- `apply_approval` runs before the commit; `deliver_webhook` runs after, so a webhook failure can never roll back the approval (matches the human path's ordering + failure isolation).
- `actor=None` (never the `/process` triggering user — that would misattribute a machine decision to a human). The distinct `"Auto-approved"` action string is the machine marker.
- The pipeline runs off the request thread (`jobs.run_pipeline_task`), so a direct, blocking `deliver_webhook` (incl. retries) adds no request latency.

## Config + env

- `config.AUTO_APPROVE_ENABLED = os.getenv("AUTO_APPROVE_ENABLED", "false").lower() in ("1","true","yes")` (mirrors `SCHEMA_AUTHOR_ENABLED`). This is the deploy-time global belt (flip env → restart → off). The **per-type DB `enabled` flag is the instant kill** — it's read fresh from the DB on every document, so disabling a type takes effect on the next document with no restart.
- `AutoApproveConfig` admin CRUD (all `require_role("admin")`), mirroring `/webhooks`:
  - `GET /auto-approve` → list configs.
  - `POST /auto-approve` (201) → `{document_type, enabled?, min_confidence}`; **409** on duplicate type; **422** if `min_confidence <= 0.9` (floor must be strictly above the review bar).
  - `PATCH /auto-approve/{id}` → `{enabled?, min_confidence?}`; 404 if missing; same `> 0.9` validation; enabling (`enabled` false→true) writes an audit-style log entry recording who enabled it.
  - `DELETE /auto-approve/{id}` (204) → 404 if missing.
- `GET /auto-approve/eval/{document_type}` (admin) → `eval.build_report(eval.correction_records(db, document_type))` for that type — feeds the enable screen's safety snapshot.

## Webhook reliability

- `deliver_webhook(config_id, document_id, approved_by)` gains **retry with backoff** (e.g. up to 3 attempts, short backoff), all within its own session/thread. On success → `doc.webhook_status="delivered"`, `webhook_detail=<code>`; on final failure → `webhook_status="failed"`, `webhook_detail=<reason>`; audit `"Webhook delivered"`/`"Webhook failed"` as today. Swallows exceptions (never raises out).
- The **human approve path** also sets `doc.webhook_status="pending"` when it schedules a delivery, so both paths get delivery visibility.
- `GET /documents/stats` gains `webhook_failed` = count of documents with `webhook_status == "failed"` (the "approved but the ERP never got it" signal).

## Frontend

- **Auto-approve admin screen** (`frontend/app/auto-approve/page.tsx`, `me()`-guarded admin, mirrors the Webhooks screen; nav link admin-only):
  - Per document type: `enabled` toggle + `min_confidence` input.
  - Shows that type's **A3 eval snapshot** from `GET /auto-approve/eval/{type}`: grounded-but-wrong rate, `n`, Wilson lower bound, and the "upper bound" caveat verbatim.
  - **Toggle disabled when `n < min_n`** ("insufficient data — do not enable").
  - A global-kill-switch state banner (read-only: "Auto-approve is globally OFF/ON").
  - Static warnings: downstream push is immediate + not un-sent by reopen; and the self-blinding note (auto-approved docs stop generating corrections, so keep golden-set curation running).
- **Queue:** an "Auto-approved" indicator on rows where `auto_approved` is true (add `auto_approved` to `serialize_summary`) + an "auto-approved only" filter (a client-side filter on the loaded page, or a query param — client-side is sufficient this slice).
- **Dashboard tiles:** `auto_approved` count, `auto_approved_reopen_rate` (auto-approved docs currently in `reopened` status ÷ total `auto_approved` — the closest "machine was wrong" proxy), and `webhook_failed` count. (Counts come from `/documents/stats` extensions, computed from the `auto_approved` / `webhook_status` columns.)

## Error handling

- Global env OFF or per-type disabled/absent → no auto-approve (short-circuits before the gate).
- `min_confidence <= 0.9` at config-set → 422.
- Webhook fails after auto-approve → retries, then `webhook_status="failed"` + audit + surfaced in the `webhook_failed` stat; the doc stays `approved` (reopen is the remediation).
- Disabling a type after some docs auto-approved is **not** retroactive (those stay approved; reopen remediates).
- `should_auto_approve` reads committed pipeline values; no recomputation.
- Reprocessing an `approved` doc is already blocked by the B11 `/process` allow-list, so an auto-approved doc can't be silently reprocessed.

## Testing (offline)

**Backend:**
- `should_auto_approve`: true only when global on + type enabled + not review_required + confidence ≥ floor; false on each missing condition (global off, no config, disabled, review_required, confidence < floor).
- `apply_approval`: sets approved + `review_required=False` + `revision++` + audit with the given action/actor; raises on an illegal transition; the human `PUT /document` approve path still works via it (existing approve tests stay green).
- Pipeline: a doc that clears the gate (stub confidence/review_required) → `status="approved"`, `"Auto-approved"` audit with `actor` null, `revision==1`; a webhook configured → `deliver_webhook` invoked with `"system:auto-approve"` and `webhook_status` transitions; global-off / type-disabled / below-floor / review_required → stays `processed`/`review_required`, no auto-approve, no webhook.
- Config API: CRUD; `min_confidence <= 0.9` → 422; duplicate type → 409; admin-only (403); enabling writes an audit entry.
- `GET /auto-approve/eval/{type}`: returns the build_report shape for the type; admin-only.
- `deliver_webhook` retry: transient failures retried then delivered → `webhook_status="delivered"`; persistent failure → `"failed"` + audit, never raises (all `requests.post` stubbed — no real network).
- `/documents/stats`: `webhook_failed` counts `webhook_status=="failed"` docs.

**Frontend (vitest):**
- The auto-approve admin screen renders per-type rows, the eval snapshot, and disables the toggle when `n` is insufficient; global-kill banner renders.
- Queue shows the auto-approved indicator / filter.
- Dashboard renders the new tiles from mocked stats.

Keep backend (210) / frontend (36) green, net new on top.

## Build sequencing (one plan, ordered tasks)

1. `AutoApproveConfig` model + migration `0007` + `AutoApprove*` schemas.
2. `Document.webhook_status`/`webhook_detail` + migration `0008`.
3. `services.apply_approval` + refactor the human `PUT /document` approve path to use it (behavior-preserving) + tests.
4. `services.should_auto_approve` + `config.AUTO_APPROVE_ENABLED` + tests.
5. Pipeline auto-approve integration (apply_approval + direct webhook dispatch + webhook_status) + tests.
6. `deliver_webhook` retry-with-backoff + `webhook_status` writes + set `pending` on the human path + tests.
7. `AutoApproveConfig` admin CRUD (`> 0.9` validation, 409/404, enable-audit) + `GET /auto-approve/eval/{type}` + tests.
8. `/documents/stats` extensions (`auto_approved`, `auto_approved_reopen_rate`, `webhook_failed`) + tests.
9. Frontend auto-approve admin screen + nav link + tests.
10. Frontend queue auto-approved indicator/filter + dashboard tiles + tests.
11. Docs: `.env.example` (`AUTO_APPROVE_ENABLED`), `docs/deployment.md`, `docs/roadmap.md` (A4 shipped; note the golden-set + threshold prerequisites), and the operational runbook for enabling a type.

## Deployment notes

Two migrations (`0007`, `0008`) run via `alembic upgrade head`. New optional env `AUTO_APPROVE_ENABLED` (default `false`) — the feature is inert until it is set AND a type is enabled AND that type clears the gate. No new dependencies. **Operational prerequisites before enabling any type in prod:** (1) curate a golden set and verify the type's grounded-but-wrong rate at the chosen floor via `scripts/eval.py`; (2) set `min_confidence` from that report (well above 0.9); (3) keep golden-set curation running after enabling (auto-approved docs no longer generate correction signal). B11 reopen is the remediation path for any bad auto-approval.
