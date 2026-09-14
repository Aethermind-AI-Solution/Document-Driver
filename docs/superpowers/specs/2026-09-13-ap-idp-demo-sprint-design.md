# AP-Focused IDP Demo Sprint — Design Spec

**Date:** 2026-09-13
**Status:** Approved design (brainstormed + red-teamed). Next step: implementation plan (`writing-plans`).
**Owner:** Solo build (developer + Claude Code).

---

## 1. Goal

Ship a **demo-ready, AP-focused Intelligent Document Processing (IDP) experience** that runs a
scripted invoice flow with *honest* confidence, highlight-on-document review, duplicate
detection, and a live ROI dashboard — and survives a prospect uploading their own
(possibly scanned) invoice.

**Primary buyer:** AP / Finance BPO. **Follow-on:** Healthcare (compliance is a *credible answer*
now, a full build next milestone).

**Timeline:** ~4–6 weeks, solo. **Budget:** free-tier during development; spend (OpenAI key,
vision, OCR, warmed instance) only once a demo is scheduled. **Format:** hybrid — scripted
"wow" flow, then an "upload one of yours" closer.

This spec supersedes nothing in `docs/roadmap.md`; it sequences a demo-targeted slice of the
existing backlog and explicitly defers the rest (see §7).

## 2. The demo narrative (the build exists to serve this)

1. **Upload** a curated invoice → pipeline runs → real per-field confidence.
2. **Review screen:** split view; click a field → it highlights the exact region on the invoice
   image; low-confidence fields flagged; approve via keyboard.
3. **Duplicate beat:** upload a near-duplicate → "⚠ possible duplicate of INV-1042" →
   *"that's a double-payment you just avoided."*
4. **ROI dashboard:** STP rate, avg review time, auto-approved count, and **buyer-editable**
   $ saved.
5. **Closer (hybrid):** "upload one of yours, and let's review it *together*" → a scanned invoice
   → OCR/vision handles it, boxes highlight, confidence stays honest, and any error becomes the
   reason the human-review step exists.
6. **Procurement question:** show the PII-redaction toggle + a written security-posture
   talk-track, with encryption/retention named as the next step that unlocks healthcare.

## 3. Workstreams (priority order)

Each workstream is built TDD-first, matching the repo's existing discipline (currently
~105 backend / ~26 frontend tests green). No regressions to shipped auth / org-isolation work.
Every AI/vision feature sits behind a flag with a **deterministic free fallback**; a single
`demo-mode` config flips OpenAI + vision + OCR on.

### W0 — De-risking spike + demo dataset (Week 1, BEFORE W2 is committed)
*Folded in from red-team. This gates the W2 design and the whole schedule.*

- **Bounding-box feasibility spike (1–2 hrs, do first):** ask the vision model for per-field
  boxes on 3 sample invoices, overlay, eyeball accuracy.
  - **Decision gate:** if <~90% of boxes land on the correct region → **do not** use
    model-direct boxes. Fall back, in order: (1) OCR engine that returns word-level boxes
    (Textract / Google Document AI / Azure Document Intelligence) + map extracted value → box by
    text match; (2) text-span highlight only (no image overlay). The W2 design is chosen by this
    gate's outcome.
- **Curated demo dataset (Week-1 dependency, not Week-5 polish):** 5–8 realistic invoices +
  one deliberate near-duplicate (for beat 3) + known-good field labels (the golden set used to
  calibrate W1). Also pull ~10 messy real-world public invoices to pressure-test the closer.
- **Golden-set check:** confirm whether a labeled set exists and its size; this decides whether
  W1 can headline a calibrated STP number (§ W1 kill criterion).

### W1 — Honest confidence (foundation)
- **Customer problem / ROI lever:** makes triage and the STP number truthful; W2 flags and W4's
  metric both lean on it.
- **Approach:** prefer a **grounding-verifier** signal (each value must appear in the document
  text/boxes) over pure self-consistency — agreement ≠ correctness. Optionally combine with a
  light self-consistency check. Recalibrate the review threshold against the W0 golden set.
- **In scope:** model-derived confidence, recalibrated review threshold, honest per-field flag.
- **Out of scope:** logprobs, bounding-box-derived confidence.
- **Kill criterion (from red-team):** if confidence cannot separate correct from wrong on the
  golden set, **do not headline a precise STP %** — show "exceptions flagged" instead.
- **Effort:** S–M.

### W2 — Document viewer + click-to-highlight + keyboard review
- **Customer problem / ROI lever:** review speed — the #1 ROI lever — *and* the visual wow.
- **Approach:** split-screen render of the uploaded PDF/image; per-field regions sourced per the
  W0 gate decision (model boxes / OCR-box+text-match / text-span); click field ↔ highlight
  region; low-confidence fields visually flagged; keyboard nav + accept.
- **In scope:** render, overlay/highlight, keyboard review loop, extractor region support +
  storage, tests.
- **Out of scope:** multi-document compare, annotation tools.
- **Effort:** L (the heaviest item).
- **Scope-overrun rule (from red-team):** if render+highlight is not demo-quality by end of
  Week 2, **cut to the 3-item plan** (W1 + W2 + W4) and drop W5/W6 from the build.

### W3 — Duplicate-invoice detection
- **Customer problem / ROI lever:** avoids double-payments — a concrete dollar-saver and a
  high-drama live beat.
- **Approach:** fingerprint on (vendor + invoice_number + amount + date); flag matches against
  the org's existing documents; surface in UI + as an anomaly chip.
- **Out of scope:** fuzzy/ML dedup, cross-tenant matching.
- **Effort:** S–M.

### W4 — Customer-facing ROI dashboard
- **Customer problem / ROI lever:** proves value; the closing asset.
- **Approach:** new screen reusing `/admin/metrics` + `/documents/stats`: STP %, avg review
  time, auto-approved count, exceptions handled, and **$ saved computed from buyer-editable
  inputs** (their monthly volume + cost/doc), showing the mechanics (STP %, time/doc), not just a
  dollar total.
- **Out of scope:** per-client historical trend lines.
- **Effort:** S–M.
- **Note (from red-team):** buyer-editable inputs are required so the number reads as *theirs*,
  not marketing math.

### W5 — OCR / scanned-doc robustness
- **Customer problem / ROI lever:** so the "upload one of yours" closer doesn't fall over.
- **Approach:** vision path for scans/photos; deskew / rotate / upscale preprocessing;
  multi-page; vision-OCR when no text layer is present. On at demo time (paid), free fallback
  for dev.
- **Out of scope:** local/offline OCR model, multi-language, handwriting.
- **Effort:** M.

### W6 — PII-redaction toggle + security-posture talk-track
- **Customer problem / ROI lever:** answers the procurement question; healthcare on-ramp.
- **Approach:** reviewer-UI toggle that masks configurable PII fields; a written security-posture
  doc for the demo.
- **Out of scope:** full encryption-at-rest, retention program, full PII redaction pipeline
  (deferred — §7).
- **Effort:** S.

## 4. Sequencing (~5 weeks, solo)

- **Week 1:** W0 (box spike + dataset + golden-set check) → W1 (confidence) + W3 (dedup).
- **Weeks 2–3:** W2 (doc viewer; design chosen by the W0 gate). **End-of-Week-2 checkpoint:**
  apply the scope-overrun rule.
- **Week 4:** W5 (OCR robustness) + W4 (ROI dashboard).
- **Week 5:** W6 (redaction + posture) + **demo hardening**: rehearse the 6 beats end-to-end,
  wire `demo-mode`, schedule a **warmed/paid demo instance** for the demo window (no Render
  free-tier cold start mid-pitch), and hold a bug buffer.

## 5. Cross-cutting rules

- **$0-dev / hot-demo:** every AI/vision/OCR feature behind a flag + deterministic free fallback;
  `demo-mode` flips the paid paths on. Spend only when a demo is scheduled.
- **Latency budget (from red-team):** the "watch it process" beat must stay under a set target
  (proposed: ≤ ~15 s for a 1–2 page invoice in demo-mode). If self-consistency (N× calls) +
  vision + per-page fan-out exceed it, reduce N or parallelize.
- **Warmed demo environment:** a paid/warmed instance reserved for the demo window; never demo on
  a cold free-tier backend.
- **Discipline:** TDD per workstream; keep the existing suites green; no regressions to shipped
  auth / org-isolation.

## 6. Definition of demo-ready

The 6-beat narrative runs end-to-end on **both** a curated invoice and a fresh scanned one;
highlights land on the right regions; the duplicate flag fires; the ROI dashboard is populated
with buyer-editable inputs; the live flow stays within the latency budget; all tests green;
`demo-mode` flips cleanly on the warmed instance.

## 7. Explicitly deferred → "Next milestone: Healthcare & AP-depth"

Carried forward with rationale so nothing is lost:

- **2-/3-way PO matching** — needs the buyer's PO/GRN data; heavy; awkward in a cold demo.
- **Vendor master matching** — depends on buyer master data.
- **Native ERP connectors** (SAP / NetSuite / QuickBooks / Oracle) — OAuth-heavy; webhook push
  already exists as the interim.
- **Full B13** — encryption-at-rest + retention + full PII-redaction program — the healthcare
  compliance gate; W6 is the interim credible answer.
- **SSO / SAML** — enterprise identity gate.
- **Durable queue** (Upstash QStash) — scale/reliability under volume, not needed for a single
  scheduled demo.
- **Multi-language, handwriting, checkbox/stamp extraction** — vertical-depth, post-demo.

## 8. Open risks being actively managed (from red-team)

| Risk | Mitigation in this spec |
|------|-------------------------|
| Vision boxes inaccurate (central wow) | W0 spike + tiered fallback before W2 commit |
| Solo 5-week scope too ambitious | End-of-Week-2 scope-overrun rule → cut to 3 items |
| "Upload your own" closer backfires live | Reframed as "review together"; pre-tested on 10 public invoices |
| Confidence not truly honest / uncalibrated | Grounding-verifier over self-consistency; golden-set calibration; STP-headline kill criterion |
| ROI "$ saved" reads as marketing math | Buyer-editable inputs; show mechanics, not just a total |
| Cold-start / latency mid-demo | Warmed paid instance + latency budget |

## 9. Things still to confirm (did not block this spec)

- Size/existence of the labeled golden set (W0 resolves).
- Sophistication of the actual prospects (tunes how much the closer and ROI math matter).
- Exact latency target for demo-mode (proposed ≤ ~15 s; confirm during W2/W5).
