# Council Review — Next-Phase Direction (Automation · Enterprise AI · Web GUI)

**Date:** 2026-08-15
**Method:** Four independent subagent perspectives (Pragmatist · Architect · Risk & Security · Product/Web-GUI) deliberated in parallel, then synthesized. Convened per the standing "council for major/important changes" preference.
**Question:** After Phase 4 F4-S1 (dynamic schemas) shipped, what is the right next direction and order — covering (1) the automation spine, (2) enterprise-grade AI, and (3) Web GUI improvements?

**Status:** Recommendation of record. Supersedes the ad-hoc ordering discussed before the council. No code changed by this review.

---

## Unanimous findings (high confidence)

1. **Do NOT ship auto-approve (B9) on the current confidence signal.** Today's confidence is an *unvalidated heuristic*: `services._ground()` returns fixed buckets (grounded 0.95 / unverified 0.70 / absent 0.55 / ungrounded 0.40) based on **substring presence**, and `document.confidence` (`agents/pipeline.py`) is a **flat average**. Consequences: a hallucinated value that coincidentally matches another token scores 0.95; one wrong critical field (e.g. `total`) is hidden by several trivial correct ones. Gating unattended approval on this pushes wrong data to ERPs via webhook.
2. **Insert a step into the automation spine.** The prior B11→B9 order was directionally right but missing **confidence calibration + an evaluation harness** *between* B11 and B9.
3. **No accuracy measurement exists anywhere in the repo** — the #1 enterprise-credibility gap. Cheap to close: a 20–50 doc golden set + reuse the learning loop's `ExtractedField.original_value` vs corrected `field_value` on approved docs to produce precision-at-confidence-threshold.
4. **Web-GUI blindness bug:** `frontend/app/page.tsx` calls `/documents` with no params then `docs.slice(0,6)` — operators cannot see past 6 documents, though the backend already supports `q`/`status`/pagination. Cheapest highest-leverage fix.
5. **Enterprise sale blocker = tenant/data isolation.** No `org_id`/`tenant_id` on any table; `GET /documents` and `GET /document/{id}` have no ownership filter, so any authenticated user (even `viewer`) can read every document + PII field. Invisible while single-tenant; disqualifying on multi-customer sale.

---

## Revised recommended sequence

### Track A — Automation spine
| # | Slice | Priority | Severity | Cost | Design note |
|---|-------|----------|----------|------|-------------|
| A1 | Frontend queue: search + status filter + pagination | P0 | High | S | Backend already supports `q`/`status`; pure UI wiring. Fixes the 6-doc cap. |
| A2 | **B11** reopen/rework + explicit document **status state machine** + reprocessing-idempotency policy | P0 | High | M | Safety-net prerequisite for B9. Also: reprocess currently deletes this doc's own correction history (field delete-then-reinsert in `pipeline.py`). |
| A3 | **Confidence calibration + eval harness** (the real "lite B4") + fix flat-average → weight/require critical fields | P0 | High | S–M | Golden set + reuse corrected-vs-original data. Keystone: unblocks B9 and backs every accuracy claim. |
| A4 | **B9** auto-approve/STP — gated: per-doc-type opt-in *default-off*, calibrated floor, validator-clean + zero anomalies, machine-vs-human audit stamp, kill switch | P1 | High | M | The payoff, de-risked. The `review_required=False` gate is already half-built in `run_pipeline`. |
| A5 | **B15** observability (lite) — counter of auto-approved-then-reopened | P1 | Med | S | Monitor STP precision in production. |

### Track B — Enterprise readiness (partly parallel to Track A)
| Slice | Priority | Severity | Cost | Note |
|-------|----------|----------|------|------|
| Tenant isolation (`org_id` + query filters + MCP token scoping) | P0 for sale | Critical | L (S to add column now) | Add the column while the schema is small. Disqualifying gap otherwise. |
| Retention/deletion (DELETE + soft-delete + storage purge) | P1 | High | M | GDPR right-to-erasure; also gives the GUI its missing delete. |
| Encryption-at-rest + LLM data-handling posture (per-type "send to LLM" toggle; OpenAI zero-retention tier; document it) | P1/P2 | High | M / S | Verbatim security-questionnaire items (esp. medical/claims docs). |
| Prompt versioning (externalize inline f-string prompts + stamp version into `pipeline_trace`) | P2 | Med | S–M | Auditability: "what prompt produced this 3 months ago?" |

### Track C — Web GUI (specific ask)
- **P0:** queue search/filter/pagination (= A1) · **source-document viewer / split-view** (M — reviewers currently correct fields without ever seeing the source; no `/document/{id}/file` route exists) · **bulk actions** (M).
- **P1:** dedicated review **workspace** (not the cramped 420px sidebar) · **line-items editable** (S — currently read-only, starving the learning loop on the most error-prone field; also fix the direct-state-mutation antipattern in `FieldBody`) · **render the audit trail** (S — `serialize()` already returns it) · **accessibility** (aria-live/`:focus-visible` absent in main workspace) · **confirmations on destructive actions** (S — Reject/Deactivate/Delete-webhook fire instantly, no undo) · **delete/archive**.
- **P2:** responsive/mobile · onboarding · richer error states (single-string, no dismiss/retry).

### Deferred / non-urgent (council agreed)
- **OCR** — parked. Scanned docs degrade *safely* today (no text layer → grounding caps at "unverified" → auto-routed to human review). Cheap future add: Tesseract to populate the grounding haystack. One P1 UX note: signal low-confidence-on-scanned instead of failing silently.
- **S2 Drive/Gmail** — held (needs OAuth; against the $0/no-card/no-OAuth constraint).

---

## Cross-cutting architectural notes (for whoever implements)
- **Document status is a bare string** mutated ad hoc across `pipeline.py`, `main.py`, `services.resolve_review_action` — no transition table. `/process/{id}` on an already-approved doc silently re-derives fields. B11 should introduce an explicit state machine before formalizing reopen.
- **Job durability:** `reset_stuck_processing` runs only on app startup; on the Render free tier a doc from a dropped BackgroundTask can sit stuck until the next cold start. Cheap fix: a `$0` scheduled trigger hitting an admin reset endpoint.
- **BackgroundTasks vs. real queue:** acceptable at MVP/$0 scale (single commit-at-end limits partial-write risk) but a documented scaling ceiling — no retry/dead-letter, in-process only.
- **Extractor** silently degrades a failed page to all-null fields with no retry (`agents/extractor.py`); one retry+backoff is a cheap reliability win.
- **Schema-Author prompt-injection** is already well-mitigated: proposed schemas are `status="suggested"` and excluded from `available_schemas()` until an admin approves — a malicious doc can at most get a weird schema *proposed*, never activated.

## Bottom line
Ship **A1 → A2 → A3 → A4 → A5**; add the `org_id` column now (cheap while small) and treat tenant isolation + retention as the gate before any multi-customer sale. The extraction pipeline's transparency (confidence/grounding/anomalies/source quotes) is already genuinely enterprise-grade — the real gaps are that you can't *prove* accuracy, auto-approve would run on an unproven signal, and the GUI is still a demo shell around a strong engine. **The eval harness + calibration (A3) is the keystone** — it unblocks B9 and backs every enterprise claim, at ~a day's cost using data already stored.
