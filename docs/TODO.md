# Aethermind — Master To-Do / Backlog

**Updated:** 2026-07-28 · Reconciles `docs/assessment.md` (B1–B16), `docs/findings-invoice-extraction.md`, the Fable deep-dive, and review-logged items against what's shipped.
Effort: XS (<½ day) · S (≤1 day) · M (1–3 days) · L (>3 days).

## ✅ Shipped
Real AI extraction (B1) · demo access gate + guardrails (B2) · honest+real confidence via grounding (B3/B4) · line-item table extraction (B7, Findings #1) · clean ₹ amounts (Findings #2) · dual GSTIN schema (Findings #3) · deploy (Vercel + Render + pinger + buildFilter) · `OPENAI_MODEL` default → gpt-4o · stat tiles fixed + real avg-time · upload progress spinner · client proposal PDF.

---

## 🔧 Quick wins / debt (small, do anytime)
| ID | Item | Effort | Why |
|----|------|--------|-----|
| Q1 | SQLite `WAL` + `busy_timeout` | XS | Removes a real "database is locked" 500 risk at 2+ concurrent users |
| Q2 | Store the **original AI value** separately from the human-corrected value | S | Currently lost on edit — **blocks the learning loop (B14)**; capture before collecting data |
| Q3 | Grounding: substring → **word-boundary** token match | S | Stops a short value ("1") matching inside "100"; tighter grounding |
| Q4 | CSV export: **flatten `line_items`** rows (currently one JSON cell) | S | ERP/AP consumers need columns, not a JSON blob |
| Q5 | `datetime.utcnow()` → tz-aware; validate `POST /schemas` payload shape (B16) | S | Deprecation + malformed-schema safety |
| Q6 | Cosmetic: add `gstin` to titleize tokens; guard all-empty-cell grounding | XS | "Seller Gstin" → "Seller GSTIN"; tidy edge |
| Q7 | Relabel the "6 agents" UI so it doesn't imply real autonomy (until real agents exist) | XS | Honesty in any demo/diligence |

## 🟠 Product depth (P1 — makes it trustworthy & real)
| ID | Item | Effort | Why |
|----|------|--------|-----|
| B5 | **Auth + roles (RBAC) + actor-stamped audit** | L | Load-bearing for any real/compliance use ("who approved this?") |
| B6 | **Persistent storage** — Postgres + object storage (R2/S3) | M | Data survives redeploys; scales past SQLite |
| B8 | **Auto-classification** agent (AI picks the document type) | M | Removes the wrong-schema failure mode |
| F4 | **Dynamic / AI-suggested schemas** (Schema-Author agent) | L | The real "not hardcoded fields" fix — proposes fields per document → human approves |
| B14 | **Learning loop** from reviewer corrections | L | Accuracy compounds — the moat (needs Q2 first) |
| B4+ | Deeper confidence — logprobs / self-consistency / **bounding boxes** | M–L | Beyond source-span grounding; visual provenance |
| B15 | Observability — structured logging, metrics, alerting | M | Trust the numbers; spot failures/spend |

## 🔵 Scale & enterprise (P2 — when volume/logos demand it)
| ID | Item | Effort | Why |
|----|------|--------|-----|
| B9 | **Straight-through processing** (auto-approve high-confidence) | M | The real cost-saving metric at scale |
| B11 | Reopen/rework + **re-process after edits** | M | Complete the review lifecycle |
| B12 | Batch/bulk upload + pagination + delete/archive | M | Operational usability |
| B10 | **Integrations** — email inbox ingestion + ERP/webhook push | L | Turns output into action; biggest adoption lever |
| B13 | Compliance layer — encryption at rest, retention, PII redaction | L | Unlocks healthcare/insurance/finance |
| — | Async processing (queue + workers) | L | Kills the ~10s synchronous upload wait |
| — | OCR path for scanned/image-only docs (non-vision fallback) | M | Vision covers text PDFs today; needed for a text-only/local model |
| — | Anomaly / cross-check agent (dup invoice, PO/price mismatch) | M | Real "agentic" value; fraud/error catch |

---

## Recommended next
1. **Quick sweep:** Q1 + Q2 + Q5 in one small pass (correctness + unblock the learning loop + debt) — ~half a day.
2. Then pick the P1 anchor: **B5 (auth + actor audit)** if heading toward a pilot, or **F4 (dynamic schemas)** / **B8 (auto-classification)** if the "adapts to any document" story is the priority.
