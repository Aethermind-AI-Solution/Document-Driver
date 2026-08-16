# A3 — Confidence Calibration + Evaluation Harness Design

**Date:** 2026-08-16
**Status:** Approved design (pre-plan)
**Roadmap item:** Slice **A3** from the 2026-08-15 council review — the "lite B4" keystone that produces the first real extraction-accuracy measurement and makes the confidence signal trustworthy enough for auto-approve (B9) to later gate on. Informed by a four-perspective design council.

## Goal

Aethermind has **no accuracy measurement** today — the #1 enterprise-credibility gap ("what's your accuracy?" has no answer) and B9's blocker. Confidence today (`services._ground`) is a fixed *presence* heuristic (value appears in the doc text → high score), not a correctness signal, and `document.confidence` is a flat mean that hides a wrong required field. A3 builds an offline **evaluation harness** that measures per-field accuracy + confidence calibration from data already in the DB, and fixes the document-confidence aggregation to be **required-field-aware** — the single reusable seam B9 will gate on. A3 is **read-only measurement + one deterministic aggregation fix**; it does not change the `_ground` constants and never auto-moves any threshold.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Ground-truth source | **Approved-doc corrections** (free, in DB): per field, `correct = not (edited_by_user and original_value != field_value)`. Golden set **supported in code** but data curation **deferred** (a B9 prerequisite). |
| Harness location | Offline pure module `backend/app/eval.py` + `scripts/eval.py` CLI. No runtime endpoint for the harness. |
| Harness outputs | Per-field accuracy (by type × field, n + Wilson lower bound + "insufficient data" under min-n); reliability table (confidence bucket → observed correctness); grounded-but-wrong rate (conf ≥ 0.9 AND later corrected). |
| Confidence fix | New `services.document_confidence(fields, schema_fields)` = **min over required fields** (fallback = mean when no required fields); replaces the flat mean at `pipeline.py:37`. The single seam B9 imports. |
| `_ground` constants | **Not recalibrated this slice** — report calibration only (changing them would bake in bias + ripple into the `<0.9` review threshold). |
| Honesty | Corrections-derived numbers labeled an **upper bound** ("agreement with unaudited approvals," never "accuracy"); Wilson lower bound, min-N gating, grounded-but-wrong first-class. Read-only — never auto-move thresholds. |
| Surfacing | `GET /documents/stats` gains `field_agreement_rate`; one dashboard tile labeled **"Fields accepted as-is"** (not "accuracy"). Dashboards/charts/trends deferred to A5. |

## Non-goals (this slice)

- Curating the golden-set **data** (loader + format ship; labeling representative docs is deferred, and is an explicit prerequisite before B9 flips auto-approve on).
- Recalibrating the `_ground` bucket constants; logprobs / self-consistency / bounding boxes (full B4).
- Picking B9's auto-approve threshold or any automatic threshold movement (A4/B9 own that, informed by — not embedded in — A3's report).
- Accuracy dashboards, charts, trend-over-time, regression alerting, reviewer-facing confidence-band UI (A5).

---

## Evaluation harness

New module `backend/app/eval.py` — pure functions, read-only, unit-testable (the math needs no DB):

```python
@dataclass
class EvalRecord:
    document_type: str
    field_name: str
    required: bool
    confidence: float
    grounded: str            # "grounded" | "unverified" | "absent" | "ungrounded"
    correct: bool
    source: str              # "corrections" | "golden"

def correction_records(db, document_type: str | None = None) -> list[EvalRecord]:
    # Approved docs' ExtractedFields. correct = not (edited_by_user and
    # original_value is not None and field_value is not None and original_value != field_value).
    # required derived from the field's schema def (services.schema_for).

def golden_records(db, fixture: dict) -> list[EvalRecord]:
    # For each doc id in the fixture with expected {field_name: value}, compare the
    # stored ExtractedField.field_value to expected (normalized). source="golden".
    # Fixture format: {"documents": [{"document_id": int, "expected": {field: value}}]}.

def per_field_accuracy(records, min_n: int = 30) -> list[dict]:
    # group by (document_type, field_name): {document_type, field_name, required, n,
    #   correct_rate, wilson_low, enough}  (enough = n >= min_n; when not enough, rate is
    #   still computed but flagged, callers show "insufficient data").

def reliability_table(records, min_n: int = 30) -> list[dict]:
    # bucket by confidence (edges aligned to the _ground constants: 0.40/0.55/0.70/0.90/0.95):
    #   {bucket, n, observed_correct_rate, wilson_low, enough}

def grounded_but_wrong_rate(records) -> dict:
    # {n_high_conf, n_wrong, rate} over records with confidence >= 0.9 — the dangerous quadrant.

def wilson_lower_bound(successes: int, n: int, z: float = 1.96) -> float:
    # standard Wilson score interval lower bound; 0.0 when n == 0.
```

`backend/scripts/eval.py` — thin CLI: opens a session via `app.database.SessionLocal`, runs the functions, prints the three tables to stdout with a mandatory honest header (`"Correction-derived agreement — unaudited-but-approved fields are counted correct; this OVERSTATES accuracy (upper bound). N=<count>."`). Flags: `--document-type`, `--min-n` (default 30), `--json <path>`, `--golden <fixture.json>` (adds golden records to the run). Not run in CI (needs real DB data); invoked manually.

**Determinism note:** the harness must not call `Date.now()`-style nondeterminism in a way that breaks tests; `wilson_lower_bound` and the aggregations are pure.

## Confidence fix

New `services.document_confidence(fields: list[dict], schema_fields: list[dict]) -> float`:
- `required_names = {f["name"] for f in schema_fields if f.get("required")}`.
- `req = [f["confidence"] for f in fields if f["field_name"] in required_names]`.
- Return `min(req)` if `req` else `sum(f["confidence"] for f in fields) / max(len(fields), 1)` (fallback for schemas with no required fields, e.g. `product_catalog`; `1.0` if there are also no fields at all).

Replace `agents/pipeline.py:37` (`document.confidence = sum(...) / max(len(ctx.fields), 1)`) with `document.confidence = services.document_confidence(ctx.fields, ctx.schema["fields"])`.

The per-field `review_required` logic (`pipeline.py:38-39`, any field conf `< .9` or not validated, or any anomaly) is **unchanged** — it already operates per-field, so a wrong required field still flags review; the fix only makes the *document-level* number honest for B9's future gate and for display.

## Surfacing (`GET /documents/stats` + one tile)

Extend `documents_stats` (`backend/app/main.py`) with `field_agreement_rate`: over all `ExtractedField` rows on **approved** documents, the fraction where `edited_by_user` is not True (i.e. accepted as-is), or `None` when there are no approved-doc fields. Add one tile to the dashboard row in `frontend/app/page.tsx` labeled **"Fields accepted as-is"** showing `Math.round(field_agreement_rate*100)%` (or "—" when null). The label deliberately does **not** say "accuracy" — it states what is literally measured (fields a reviewer left unedited), honoring the honesty guardrail.

## Error handling

- Empty corrections sample → harness returns empty tables / `field_agreement_rate = None`; the CLI prints "insufficient data"; the tile shows "—".
- Buckets/fields under `min_n` → flagged `enough=False`; callers render "insufficient data" rather than a falsely precise percentage.
- `golden_records` with a malformed fixture → raise a clear error in the CLI (fixture is developer-provided, not user input).
- The harness is read-only: no writes, no threshold changes, no effect on the live pipeline beyond the `document_confidence` aggregation.

## Testing (offline)

**Backend (`backend/tests/test_a3_eval.py`, pure — synthetic `EvalRecord`s, no DB):**
- `wilson_lower_bound`: known values (e.g. 8/10 → ~0.49; 0/0 → 0.0; monotonic in n).
- `per_field_accuracy`: groups by type×field; `enough` false under `min_n`; correct_rate math.
- `reliability_table`: buckets align to the `_ground` edges; observed rate per bucket; min-n flag.
- `grounded_but_wrong_rate`: counts only conf ≥ 0.9; rate over that subset; the "high-confidence but corrected" case is caught.

**Backend (DB-seeded):**
- `correction_records`: seed approved docs with a mix of edited (`original != field_value`) and unedited fields → correct/incorrect labels + `required` from schema; non-approved docs excluded.
- `golden_records`: a tiny fixture (2 docs, a right + a wrong field) → correct labels.
- `services.document_confidence`: min-over-required (a 0.40 required field ⇒ doc 0.40 even with several 0.95 optional fields); no-required-fields fallback to mean; empty → 1.0.
- Pipeline: `document.confidence` now comes from `document_confidence` (an integration check that a wrong required field yields a low doc confidence).
- `GET /documents/stats` includes `field_agreement_rate` computed over approved-doc fields (seed edited/unedited → expected fraction; `None` when no approved docs).

**Frontend (vitest):** the "Fields accepted as-is" tile renders the value from a mocked `/documents/stats` (and "—" when null).

Keep backend (195) / frontend (35) green, net new on top.

## Build sequencing (one plan, ordered tasks)

1. `services.document_confidence` + wire into `pipeline.py:37`, with tests (min-over-required, fallback, pipeline integration).
2. `backend/app/eval.py` pure functions (`wilson_lower_bound`, `per_field_accuracy`, `reliability_table`, `grounded_but_wrong_rate`) + `EvalRecord`, with synthetic-data tests.
3. `correction_records` + `golden_records` (DB-sourced) + a tiny golden fixture, with seeded-DB tests.
4. `scripts/eval.py` CLI (honest header, flags, tables/JSON).
5. `GET /documents/stats` `field_agreement_rate` + the "Fields accepted as-is" dashboard tile, with tests.
6. Docs: `docs/roadmap.md` (A3 shipped; next A4), `docs/deployment.md` (how to run `scripts/eval.py`; note the golden-set curation is a B9 prerequisite).

## Deployment notes

No migration, no new dependencies (Wilson bound is arithmetic; no scipy), no new env vars. `document_confidence` changes how the document-level number is computed going forward (existing rows keep their stored value until reprocessed). `scripts/eval.py` is an operator/dev tool run manually against the DB. The `field_agreement_rate` addition to `/documents/stats` is additive. The golden-set **data** must be curated before B9 auto-approve is enabled (documented, not built here).
