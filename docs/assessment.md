# Aethermind — Workflow Assessment, AI Role & Prioritized Backlog

**Date:** 2026-07-24
**Status:** MVP deployed (Vercel + Render); assessment for planning next steps.

---

## 1. What the AI actually does (its real role)

**The AI has exactly one job: field extraction.** Given a document + a schema
(a list of fields), it reads the document and returns a value for each field.

`backend/app/services.py` has two extraction paths:

| Mode | When | What it is |
|---|---|---|
| **AI mode** (`ai_extract`) | `OPENAI_API_KEY` is set | OpenAI Responses API (`gpt-5`). Sends the PDF/image (base64) + extracted text, requests **strict JSON-schema output**, returns structured field values. Real document understanding — handles varied layouts, no templates. |
| **Fallback mode** (`fallback_extract`) | No key | **Regex / label matching** (`"Invoice No: ___"`). Deterministic, brittle, no AI. |

### The honest headline: the live demo is NOT running AI
The deployed Render backend has **no `OPENAI_API_KEY`**, so it currently uses the
**regex fallback** (that's why `vendor_name` came back empty in testing). Real AI
only switches on when a key is added.

### What the AI does NOT do (misconceptions to correct)
- **Classification** — the *user* picks the document type. Wrong pick → garbage extraction.
- **Validation** — rule-based (`passes_validation`: required/regex/enum), not AI.
- **Confidence scores** — **hardcoded constants** (`0.94`, `0.62`…), *not* model-derived.
- **The "6 agents"** (Intake → … → Export) — **cosmetic UI narration**, played
  client-side. No real autonomous agents, no per-agent AI calls.

**Summary:** today AI = a single "extract these fields" call, and it's switched off in the demo.

---

## 2. Weaknesses in the current workflow

### Credibility-breaking (fix before this is "real")
- **W1 — Fake confidence.** The whole review trigger (`review_required = confidence < 0.9`)
  runs on hardcoded numbers, not real uncertainty. A "94% confident" field could be a hallucination.
- **W2 — No AI grounding/verification.** Even in AI mode, no check that a value
  appears in the document (no citations/source spans/bounding boxes). Can't tell
  a correct value from a confident hallucination.
- **W3 — Demo runs on regex, not AI.** Fine for $0 demo, but don't pitch it as "AI-powered" while the key is off.

### Security & compliance (serious for these document types)
- **W4 — No authentication.** The API is wide open: anyone with the URL can
  upload, process, edit, delete, and read every document.
- **W5 — Sensitive data, zero protection.** Schemas include **Medical Record**
  and **Insurance Claim** (PII/PHI), stored plaintext in SQLite + local files —
  no encryption, access control, or retention policy. Compliance non-starter (HIPAA/GDPR).
- **W6 — No audit actor.** Logs record "Approved"/"Rejected" but not *who*. Fatal for a compliance audit trail.
- **W7 — Cost/abuse exposure.** Open endpoints + an OpenAI key = anyone can drive
  up the API bill. No rate limiting, file-size limits, or upload scanning.

### Data & scale
- **W8 — Ephemeral storage.** SQLite + local `uploads/` reset on every redeploy (no persistent disk on free tier).
- **W9 — SQLite doesn't scale** (single-writer; concurrent processing contends).
- **W10 — No pagination / weak search** (filename `ilike` only), **no delete/archive**.

### Workflow completeness
- **W11 — Approve overrides validation** (forces `validated:true` on all fields).
- **W12 — Reject is terminal** — no reopen/rework, no re-processing after edits.
- **W13 — No batch/bulk upload.**
- **W14 — No line-item/table extraction** (invoice `line_items` array declared, never extracted/rendered).
- **W15 — Thin schema validation** (`POST /schemas` accepts arbitrary JSON).
- **W16 — No real integrations** — the "ERP payload" is just a CSV.
- **W17 — Hardcoded dashboard metrics** ("18 sec", 92%).

### Hygiene
- **W18 —** `datetime.utcnow()` deprecated (3 uses); no structured logging/monitoring;
  multi-page docs truncated at `text[:50000]`.

---

## 3. Future potential

The core idea — a **schema-driven, configurable extraction pipeline with
human-in-the-loop review** — is exactly how real Intelligent Document Processing
(IDP) products are built. The bones are right; the depth is MVP-shallow.

### Near-term (make the AI real)
- Turn on real AI **and** derive **real confidence** (verifier pass / model signals) so review triage means something.
- **Grounding/citations** — return the source span/bounding box per value.
- **Auto-classification** — AI picks the doc type (removes the wrong-schema failure mode).
- **Line-item/table extraction** — biggest functional gap for invoices/POs.
- **Learning loop** — feed reviewer corrections back as few-shot/fine-tuning → accuracy compounds (the moat).

### Product direction (where the value is)
- **Straight-through processing** — auto-approve high-confidence, route only
  exceptions to humans → cost-per-document drops (the real BPO value).
- **Genuinely agentic pipeline** — real agents: classify → extract →
  cross-check against ERP/vendor DB → flag anomalies (duplicate invoice, price/PO mismatch).
- **Integrations** — ERP (SAP/NetSuite/QuickBooks), webhooks, RPA.
- **Compliance layer** — auth + RBAC + encryption + retention + PII redaction →
  unlocks regulated verticals (healthcare, insurance, finance).
- **Schema marketplace** — pre-built, tuned templates per industry.

### Market reality
IDP is a large, active market (Rossum, Nanonets, Docsumo, plus Azure Document
Intelligence / AWS Textract / Google Document AI). Plausible edge: the
**configurable schema layer + polished human-in-the-loop UX + agentic verification**,
not raw OCR (commoditized by the giants).

---

## 4. Prioritized backlog

Priority = value-before-this-is-trusted. Effort: S (<½ day), M (1–3 days), L (>3 days).

### P0 — Before sharing/pitching (credibility & safety of a public demo)
| ID | Item | Effort | Why now |
|----|------|--------|---------|
| B1 | Turn on real AI (`OPENAI_API_KEY`) so extraction actually works | S | The demo currently returns empty fields; real AI is the "wow". (W3) |
| B2 | Protect the public backend — API key/gate + upload size limit + basic rate limit | M | Open endpoints on a shared URL = anyone can delete data or run up the OpenAI bill. (W4, W7) |
| B3 | Honest confidence in the UI (or label it "heuristic") until W1 is real | S | Don't present fabricated confidence as model output. (W1) |

### P1 — Make it trustworthy / product-real
| ID | Item | Effort | Why |
|----|------|--------|-----|
| B4 | **Real confidence + grounding/citations** | L | The whole human-in-the-loop premise depends on it. (W1, W2) |
| B5 | **Authentication + roles + actor-stamped audit** | L | Required for any compliance/BPO story. (W4, W6) |
| B6 | Persistent storage — Postgres + object storage (S3) | M | Data survives redeploys; scales past SQLite. (W8, W9) |
| B7 | Line-item / table extraction (render + validate arrays) | M | Biggest functional gap for invoices/POs. (W14) |
| B8 | Auto-classification (AI picks doc type) | M | Removes the wrong-schema failure mode. |

### P2 — Scale & depth
| ID | Item | Effort | Why |
|----|------|--------|-----|
| B9 | Straight-through processing (auto-approve high-confidence) | M | The real cost-saving metric. |
| B10 | Real integrations (ERP/webhook push) | L | Turns output into action. (W16) |
| B11 | Reopen/rework + re-processing after edits | M | Complete the review lifecycle. (W12) |
| B12 | Batch/bulk upload + pagination + delete/archive | M | Operational usability. (W10, W13) |
| B13 | Compliance layer (encryption, retention, PII redaction) | L | Unlocks healthcare/insurance/finance. (W5) |
| B14 | Learning loop from reviewer corrections | L | Accuracy compounds; the moat. |
| B15 | Real dashboard metrics + structured logging/monitoring | M | Trust the numbers; observability. (W17, W18) |
| B16 | Hygiene: `datetime.utcnow` → tz-aware; schema-payload validation | S | Debt cleanup. (W15, W18) |

---

## 5. Recommended next steps

*(Sequencing to be finalized based on the audience for "sending this".)*

1. **If sharing a demo for feedback:** do **B1 → B3 → B2** (make AI real, be honest
   about confidence, lock the door) — small effort, big credibility.
2. **If moving toward a real product/pilot:** then **B4 (real confidence+grounding)**
   and **B5 (auth+audit)** are the load-bearing pillars everything else needs.
3. Each item goes through the standard flow: brainstorm → spec → plan → TDD → verify.
