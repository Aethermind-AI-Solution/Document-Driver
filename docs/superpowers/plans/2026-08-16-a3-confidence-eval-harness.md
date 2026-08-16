# A3 — Confidence Calibration + Evaluation Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline evaluation harness that measures per-field extraction accuracy + confidence calibration from existing DB data, and fix document-level confidence to be required-field-aware (the seam B9 will gate on).

**Architecture:** A pure, read-only `backend/app/eval.py` (accuracy/reliability math over `EvalRecord`s sourced from approved-doc corrections and an optional golden fixture) with a `scripts/eval.py` CLI. `services.document_confidence(fields, schema_fields)` replaces the flat mean at `pipeline.py:37` with a min-over-required rule. `/documents/stats` gains an honestly-labeled `field_agreement_rate` surfaced as one dashboard tile.

**Tech Stack:** FastAPI + SQLAlchemy (backend), Next.js 15 / React 19 / TypeScript / Tailwind (frontend), pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-08-16-a3-confidence-eval-harness-design.md`

## Global Constraints

- Harness is **read-only**: no writes, no threshold changes, no auto-calibration. It reports; humans decide.
- Ground truth = **approved-doc corrections**: `correct = not (edited_by_user and original_value is not None and field_value is not None and original_value != field_value)`. Golden fixture **loader + format ship**; curating the data is deferred (a B9 prerequisite).
- Corrections-derived numbers are an **upper bound** — label them "agreement with unaudited approvals," never "accuracy". Min-N gating (`enough = n >= min_n`, default 30) and a **Wilson lower bound** accompany every rate; the **grounded-but-wrong** rate (conf ≥ 0.9 AND not correct) is a first-class output.
- `document_confidence` = **min over required fields**; fallback = mean of all fields when a schema has no required fields; `1.0` when no fields. It is the single seam B9 imports.
- Do NOT recalibrate the `_ground` bucket constants. No migration, no new dependencies (Wilson bound is arithmetic — no scipy), no new env vars.
- The `field_agreement_rate` tile is labeled **"Fields accepted as-is"** — never "accuracy".
- Keep backend **195** / frontend **35** tests green (net new on top).

---

### Task 1: `services.document_confidence` + wire into the pipeline

**Files:**
- Modify: `backend/app/services.py` (add `document_confidence`)
- Modify: `backend/app/agents/pipeline.py:37`
- Test: `backend/tests/test_a3_eval.py` (new)

**Interfaces:**
- Produces: `services.document_confidence(fields: list[dict], schema_fields: list[dict]) -> float`.

- [ ] **Step 1: Write the failing tests.** Create `backend/tests/test_a3_eval.py`:

```python
from app import services


def test_document_confidence_min_over_required():
    schema = [{"name": "total", "required": True}, {"name": "notes"}]
    fields = [{"field_name": "total", "confidence": 0.4}, {"field_name": "notes", "confidence": 0.95}]
    assert services.document_confidence(fields, schema) == 0.4


def test_document_confidence_wrong_required_not_averaged_away():
    schema = [{"name": "total", "required": True}, {"name": "a"}, {"name": "b"}, {"name": "c"}]
    fields = [{"field_name": "total", "confidence": 0.4}] + \
             [{"field_name": n, "confidence": 0.95} for n in ("a", "b", "c")]
    assert services.document_confidence(fields, schema) == 0.4


def test_document_confidence_fallback_mean_when_no_required():
    schema = [{"name": "a"}, {"name": "b"}]
    fields = [{"field_name": "a", "confidence": 0.8}, {"field_name": "b", "confidence": 0.6}]
    assert abs(services.document_confidence(fields, schema) - 0.7) < 1e-9


def test_document_confidence_empty_is_one():
    assert services.document_confidence([], []) == 1.0
```

- [ ] **Step 2: Run the tests to verify they fail.** Run (from `backend/`): `python3 -m pytest tests/test_a3_eval.py -q`
Expected: FAIL — `AttributeError: module 'app.services' has no attribute 'document_confidence'`.

- [ ] **Step 3: Implement.** In `backend/app/services.py`, add (near `_ground_fields`, module level):

```python
def document_confidence(fields: list[dict], schema_fields: list[dict]) -> float:
    """Document-level confidence = min over REQUIRED fields, so a wrong required
    field cannot be averaged away. Falls back to the mean of all fields when the
    schema has no required fields; 1.0 when there are no fields at all."""
    required_names = {f["name"] for f in schema_fields if f.get("required")}
    req = [f["confidence"] for f in fields if f["field_name"] in required_names]
    if req:
        return min(req)
    if fields:
        return sum(f["confidence"] for f in fields) / len(fields)
    return 1.0
```

- [ ] **Step 4: Wire it into the pipeline.** In `backend/app/agents/pipeline.py`, replace line 37:

```python
        document.confidence = sum(f["confidence"] for f in ctx.fields) / max(len(ctx.fields), 1)
```

with:

```python
        document.confidence = services.document_confidence(ctx.fields, ctx.schema["fields"])
```

(`services` is already imported in `pipeline.py`.)

- [ ] **Step 5: Run the tests.** Run (from `backend/`): `python3 -m pytest tests/test_a3_eval.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full suite.** Run (from `backend/`): `python3 -m pytest -q`
Expected: all green (199 = 195 + 4 new). Existing pipeline tests still pass (the per-field `review_required` logic is unchanged; only the document-level number changes — if a pipeline test asserts an exact `document.confidence` value, update it to the min-over-required result and note it in your report).

- [ ] **Step 7: Commit.**

```bash
git add backend/app/services.py backend/app/agents/pipeline.py backend/tests/test_a3_eval.py
git commit -m "feat: document_confidence = min over required fields (replaces flat mean)"
```

---

### Task 2: `eval.py` pure functions

**Files:**
- Create: `backend/app/eval.py`
- Test: `backend/tests/test_a3_eval.py` (append)

**Interfaces:**
- Produces: `eval.EvalRecord`; `wilson_lower_bound(successes, n, z=1.96) -> float`; `per_field_accuracy(records, min_n=30) -> list[dict]`; `reliability_table(records, min_n=30) -> list[dict]`; `grounded_but_wrong_rate(records) -> dict`; `build_report(records, min_n=30) -> dict`.

- [ ] **Step 1: Write the failing tests.** Append to `backend/tests/test_a3_eval.py`:

```python
from app import eval as evalmod
from app.eval import EvalRecord


def _rec(dt="invoice", fn="total", required=True, conf=0.95, grounded="grounded", correct=True, source="corrections"):
    return EvalRecord(dt, fn, required, conf, grounded, correct, source)


def test_wilson_lower_bound():
    assert evalmod.wilson_lower_bound(0, 0) == 0.0
    assert 0.44 < evalmod.wilson_lower_bound(8, 10) < 0.50
    # more samples at the same rate → tighter (higher) lower bound
    assert evalmod.wilson_lower_bound(80, 100) > evalmod.wilson_lower_bound(8, 10)


def test_per_field_accuracy_groups_and_min_n():
    recs = [_rec(correct=True)] * 3 + [_rec(correct=False)]
    out = evalmod.per_field_accuracy(recs, min_n=30)
    row = next(r for r in out if r["field_name"] == "total")
    assert row["n"] == 4 and abs(row["correct_rate"] - 0.75) < 1e-9 and row["enough"] is False
    assert row["required"] is True


def test_reliability_table_buckets_align_to_ground_constants():
    recs = [_rec(conf=0.95, correct=True), _rec(conf=0.95, correct=False), _rec(conf=0.40, correct=False)]
    table = evalmod.reliability_table(recs, min_n=1)
    b95 = next(b for b in table if b["bucket"] == "0.95")
    assert b95["n"] == 2 and abs(b95["observed_correct_rate"] - 0.5) < 1e-9
    b40 = next(b for b in table if b["bucket"] == "0.40")
    assert b40["n"] == 1 and b40["observed_correct_rate"] == 0.0
    # empty bucket → n 0, rate None
    b70 = next(b for b in table if b["bucket"] == "0.70")
    assert b70["n"] == 0 and b70["observed_correct_rate"] is None


def test_grounded_but_wrong_rate():
    recs = [_rec(conf=0.95, correct=True), _rec(conf=0.95, correct=False), _rec(conf=0.5, correct=False)]
    gbw = evalmod.grounded_but_wrong_rate(recs)
    assert gbw["n_high_conf"] == 2 and gbw["n_wrong"] == 1 and abs(gbw["rate"] - 0.5) < 1e-9


def test_build_report_shape():
    rep = evalmod.build_report([_rec()], min_n=1)
    assert set(rep) >= {"n", "per_field", "reliability", "grounded_but_wrong", "header"}
    assert "upper bound" in rep["header"].lower()
```

- [ ] **Step 2: Run the tests to verify they fail.** Run (from `backend/`): `python3 -m pytest tests/test_a3_eval.py -k "wilson or per_field or reliability or grounded or build_report" -q`
Expected: FAIL — `No module named 'app.eval'`.

- [ ] **Step 3: Implement.** Create `backend/app/eval.py`:

```python
from dataclasses import dataclass
from math import sqrt

# Bucket edges aligned to the fixed _ground confidence constants.
_BUCKETS = [(0.0, 0.475, "0.40"), (0.475, 0.625, "0.55"), (0.625, 0.80, "0.70"),
            (0.80, 0.925, "0.90"), (0.925, 1.01, "0.95")]
_BUCKET_LABELS = ["0.40", "0.55", "0.70", "0.90", "0.95"]


@dataclass
class EvalRecord:
    document_type: str
    field_name: str
    required: bool
    confidence: float
    grounded: str
    correct: bool
    source: str            # "corrections" | "golden"


def wilson_lower_bound(successes: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    phat = successes / n
    denom = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def _bucket(conf: float) -> str:
    for lo, hi, label in _BUCKETS:
        if lo <= conf < hi:
            return label
    return "0.95" if conf >= 0.925 else "0.40"


def per_field_accuracy(records, min_n: int = 30) -> list[dict]:
    groups: dict = {}
    for r in records:
        groups.setdefault((r.document_type, r.field_name), []).append(r)
    out = []
    for (dt, fn), rs in groups.items():
        n = len(rs)
        correct = sum(1 for r in rs if r.correct)
        out.append({"document_type": dt, "field_name": fn, "required": rs[0].required,
                    "n": n, "correct_rate": correct / n if n else 0.0,
                    "wilson_low": wilson_lower_bound(correct, n), "enough": n >= min_n})
    return sorted(out, key=lambda d: (d["document_type"], d["field_name"]))


def reliability_table(records, min_n: int = 30) -> list[dict]:
    buckets: dict = {}
    for r in records:
        buckets.setdefault(_bucket(r.confidence), []).append(r)
    out = []
    for label in _BUCKET_LABELS:
        rs = buckets.get(label, [])
        n = len(rs)
        correct = sum(1 for r in rs if r.correct)
        out.append({"bucket": label, "n": n,
                    "observed_correct_rate": (correct / n) if n else None,
                    "wilson_low": wilson_lower_bound(correct, n), "enough": n >= min_n})
    return out


def grounded_but_wrong_rate(records) -> dict:
    hi = [r for r in records if r.confidence >= 0.9]
    n = len(hi)
    wrong = sum(1 for r in hi if not r.correct)
    return {"n_high_conf": n, "n_wrong": wrong, "rate": (wrong / n) if n else None}


def build_report(records, min_n: int = 30) -> dict:
    return {
        "n": len(records),
        "header": (f"Correction-derived agreement — unaudited-but-approved fields are counted "
                   f"correct; this OVERSTATES accuracy (upper bound). N={len(records)}."),
        "per_field": per_field_accuracy(records, min_n),
        "reliability": reliability_table(records, min_n),
        "grounded_but_wrong": grounded_but_wrong_rate(records),
    }
```

- [ ] **Step 4: Run the tests.** Run (from `backend/`): `python3 -m pytest tests/test_a3_eval.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite.** Run (from `backend/`): `python3 -m pytest -q`
Expected: all green (205 = 199 + 6 new).

- [ ] **Step 6: Commit.**

```bash
git add backend/app/eval.py backend/tests/test_a3_eval.py
git commit -m "feat: eval.py — Wilson bound, per-field accuracy, reliability table, grounded-but-wrong"
```

---

### Task 3: DB-sourced records (`correction_records`, `golden_records`) + golden fixture format

**Files:**
- Modify: `backend/app/eval.py` (add DB-sourced functions)
- Create: `backend/scripts/golden_set.example.json` (format template — no real data)
- Test: `backend/tests/test_a3_eval.py` (append)

**Interfaces:**
- Consumes: `EvalRecord` (Task 2); `services.schema_for`, `services._norm`.
- Produces: `eval.correction_records(db, document_type=None) -> list[EvalRecord]`; `eval.golden_records(db, fixture: dict) -> list[EvalRecord]`.

- [ ] **Step 1: Write the failing tests.** Append to `backend/tests/test_a3_eval.py`:

```python
from app.models import Document, ExtractedField


def _approved_doc(db, dt="invoice"):
    d = Document(filename="a.pdf", document_type=dt, stored_path="p", status="approved", review_required=False)
    db.add(d); db.commit(); db.refresh(d)
    return d


def test_correction_records_labels_correct(db_session):
    d = _approved_doc(db_session)
    # edited (wrong) required field, and an unedited field (presumed correct)
    db_session.add(ExtractedField(document_id=d.id, field_name="total", field_value="100",
                                  original_value="90", edited_by_user=True, confidence=0.95, grounded="grounded"))
    db_session.add(ExtractedField(document_id=d.id, field_name="vendor_name", field_value="Acme",
                                  original_value="Acme", edited_by_user=False, confidence=0.9, grounded="grounded"))
    db_session.commit()
    recs = evalmod.correction_records(db_session)
    by = {r.field_name: r for r in recs}
    assert by["total"].correct is False and by["total"].required is True
    assert by["vendor_name"].correct is True


def test_correction_records_excludes_non_approved(db_session):
    d = Document(filename="b.pdf", document_type="invoice", stored_path="p", status="review_required")
    db_session.add(d); db_session.commit(); db_session.refresh(d)
    db_session.add(ExtractedField(document_id=d.id, field_name="total", field_value="1",
                                  original_value="1", confidence=0.9)); db_session.commit()
    assert evalmod.correction_records(db_session) == []


def test_golden_records_compares_to_expected(db_session):
    d = _approved_doc(db_session)
    db_session.add(ExtractedField(document_id=d.id, field_name="total", field_value="100",
                                  original_value="100", confidence=0.95, grounded="grounded"))
    db_session.add(ExtractedField(document_id=d.id, field_name="invoice_number", field_value="INV-1",
                                  original_value="INV-1", confidence=0.9, grounded="grounded"))
    db_session.commit()
    fixture = {"documents": [{"document_id": d.id, "expected": {"total": "100", "invoice_number": "WRONG"}}]}
    recs = {r.field_name: r for r in evalmod.golden_records(db_session, fixture)}
    assert recs["total"].correct is True and recs["total"].source == "golden"
    assert recs["invoice_number"].correct is False
```

- [ ] **Step 2: Run the tests to verify they fail.** Run (from `backend/`): `python3 -m pytest tests/test_a3_eval.py -k "correction_records or golden_records" -q`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Implement.** Add to `backend/app/eval.py` (top, add imports; bottom, the functions):

```python
from . import services
from .models import Document, ExtractedField
```

```python
def correction_records(db, document_type: str | None = None) -> list[EvalRecord]:
    """EvalRecords from APPROVED documents' fields. correct = the AI value was NOT
    later changed by a human (unedited fields are presumed — weakly — correct)."""
    q = (db.query(ExtractedField, Document.document_type)
         .join(Document, ExtractedField.document_id == Document.id)
         .filter(Document.status == "approved"))
    if document_type:
        q = q.filter(Document.document_type == document_type)
    schema_cache: dict = {}
    out = []
    for ef, dt in q.all():
        if dt not in schema_cache:
            try:
                schema_cache[dt] = {f["name"]: f for f in services.schema_for(db, dt)["fields"]}
            except Exception:
                schema_cache[dt] = {}
        required = bool(schema_cache[dt].get(ef.field_name, {}).get("required"))
        correct = not (ef.edited_by_user and ef.original_value is not None
                       and ef.field_value is not None and ef.original_value != ef.field_value)
        out.append(EvalRecord(dt, ef.field_name, required, ef.confidence,
                              ef.grounded or "unverified", correct, "corrections"))
    return out


def golden_records(db, fixture: dict) -> list[EvalRecord]:
    """EvalRecords comparing stored extraction against a hand-labeled golden fixture:
    {"documents": [{"document_id": int, "expected": {field_name: value}}]}."""
    out = []
    for entry in fixture.get("documents", []):
        doc = db.get(Document, entry["document_id"])
        if not doc:
            continue
        try:
            schema = {f["name"]: f for f in services.schema_for(db, doc.document_type)["fields"]}
        except Exception:
            schema = {}
        by_name = {ef.field_name: ef for ef in doc.extracted_fields}
        for fname, expected in (entry.get("expected") or {}).items():
            ef = by_name.get(fname)
            if ef is None:
                continue
            required = bool(schema.get(fname, {}).get("required"))
            correct = services._norm(str(ef.field_value)) == services._norm(str(expected)) \
                if ef.field_value is not None else (expected in (None, ""))
            out.append(EvalRecord(doc.document_type, fname, required, ef.confidence,
                                  ef.grounded or "unverified", bool(correct), "golden"))
    return out
```

- [ ] **Step 4: Add the fixture template.** Create `backend/scripts/golden_set.example.json`:

```json
{
  "_comment": "Golden set format for scripts/eval.py --golden. Curate real entries before enabling B9 auto-approve. Each document references an existing document_id and its human-verified expected field values.",
  "documents": [
    { "document_id": 0, "expected": { "total": "1234.56", "invoice_number": "INV-001" } }
  ]
}
```

- [ ] **Step 5: Run the tests.** Run (from `backend/`): `python3 -m pytest tests/test_a3_eval.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full suite.** Run (from `backend/`): `python3 -m pytest -q`
Expected: all green (208 = 205 + 3 new).

- [ ] **Step 7: Commit.**

```bash
git add backend/app/eval.py backend/scripts/golden_set.example.json backend/tests/test_a3_eval.py
git commit -m "feat: correction_records + golden_records (DB-sourced) + golden fixture format"
```

---

### Task 4: `scripts/eval.py` CLI

**Files:**
- Create: `backend/scripts/eval.py`
- Test: `backend/tests/test_a3_eval.py` (append)

**Interfaces:**
- Consumes: `eval.correction_records`, `eval.golden_records`, `eval.build_report`; `app.database.SessionLocal`.

- [ ] **Step 1: Implement the CLI.** Create `backend/scripts/eval.py`:

```python
"""Offline extraction-accuracy report. Run from backend/: python scripts/eval.py [--json out.json]

The numbers are CORRECTION-DERIVED (unaudited-but-approved fields count as correct) and
therefore OVERSTATE accuracy — treat as an upper bound. Curate a golden set (--golden) for an
unbiased anchor before relying on these for auto-approve (B9)."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `app` importable

from app.database import SessionLocal
from app import eval as evalmod


def run(db, document_type=None, min_n=30, golden_path=None) -> dict:
    records = evalmod.correction_records(db, document_type)
    if golden_path:
        with open(golden_path) as fh:
            records += evalmod.golden_records(db, json.load(fh))
    return evalmod.build_report(records, min_n)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Extraction-accuracy report (correction-derived; upper bound).")
    ap.add_argument("--document-type")
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--json", dest="json_path")
    ap.add_argument("--golden", dest="golden_path")
    args = ap.parse_args(argv)
    db = SessionLocal()
    try:
        report = run(db, args.document_type, args.min_n, args.golden_path)
    finally:
        db.close()
    print(report["header"])
    print("\n== Per-field agreement ==")
    for r in report["per_field"]:
        flag = "" if r["enough"] else "  (insufficient data)"
        print(f"  {r['document_type']}/{r['field_name']}: {r['correct_rate']:.0%} "
              f"(n={r['n']}, wilson≥{r['wilson_low']:.0%}){flag}")
    print("\n== Reliability (confidence bucket → observed agreement) ==")
    for b in report["reliability"]:
        rate = "n/a" if b["observed_correct_rate"] is None else f"{b['observed_correct_rate']:.0%}"
        flag = "" if b["enough"] else "  (insufficient data)"
        print(f"  conf {b['bucket']}: {rate} (n={b['n']}){flag}")
    g = report["grounded_but_wrong"]
    grate = "n/a" if g["rate"] is None else f"{g['rate']:.0%}"
    print(f"\n== Grounded-but-wrong (conf>=0.9 yet later corrected): {grate} "
          f"({g['n_wrong']}/{g['n_high_conf']}) ==")
    if args.json_path:
        with open(args.json_path, "w") as fh:
            json.dump(report, fh, indent=2)
        print(f"\nWrote {args.json_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write the failing test.** Append to `backend/tests/test_a3_eval.py`:

```python
import importlib.util
from pathlib import Path

_CLI = Path(__file__).resolve().parents[1] / "scripts" / "eval.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("a3_eval_cli", _CLI)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def test_cli_run_builds_report(db_session, tmp_path):
    d = _approved_doc(db_session)
    db_session.add(ExtractedField(document_id=d.id, field_name="total", field_value="1",
                                  original_value="2", edited_by_user=True, confidence=0.95, grounded="grounded"))
    db_session.commit()
    cli = _load_cli()
    report = cli.run(db_session, min_n=1)
    assert report["n"] == 1 and "upper bound" in report["header"].lower()
    assert report["grounded_but_wrong"]["n_wrong"] == 1
```

- [ ] **Step 3: Run the test.** Run (from `backend/`): `python3 -m pytest tests/test_a3_eval.py -k cli -q`
Expected: PASS (the `run(db, …)` helper is DB-driven and pure; `main()`'s argv/print path is exercised manually, not in CI).

- [ ] **Step 4: Run the full suite + a manual smoke.** Run (from `backend/`): `python3 -m pytest -q` (expect 209 = 208 + 1 new). Then a manual smoke (won't be committed): `python scripts/eval.py --min-n 1` prints the header + tables without error (empty DB → "insufficient data" / n/a).

- [ ] **Step 5: Commit.**

```bash
git add backend/scripts/eval.py backend/tests/test_a3_eval.py
git commit -m "feat: scripts/eval.py CLI — accuracy report with honest upper-bound header"
```

---

### Task 5: `field_agreement_rate` in `/documents/stats` + dashboard tile

**Files:**
- Modify: `backend/app/main.py` (`documents_stats`)
- Modify: `frontend/app/page.tsx` (stats type + tiles)
- Test: `backend/tests/test_a3_eval.py` (append) + `frontend/app/field-agreement.test.tsx` (new)

**Interfaces:**
- Produces: `GET /documents/stats` gains `field_agreement_rate: float | null`.

- [ ] **Step 1: Write the failing backend test.** Append to `backend/tests/test_a3_eval.py`:

```python
def test_stats_includes_field_agreement_rate(client, db_session):
    d = _approved_doc(db_session)
    db_session.add(ExtractedField(document_id=d.id, field_name="total", field_value="1",
                                  original_value="1", edited_by_user=False, confidence=0.9))
    db_session.add(ExtractedField(document_id=d.id, field_name="tax", field_value="2",
                                  original_value="0", edited_by_user=True, confidence=0.9))
    db_session.commit()
    s = client.get("/documents/stats").json()
    assert abs(s["field_agreement_rate"] - 0.5) < 1e-9   # 1 of 2 fields unedited


def test_stats_field_agreement_none_when_no_approved(client, db_session):
    s = client.get("/documents/stats").json()
    assert s["field_agreement_rate"] is None
```

- [ ] **Step 2: Run the tests to verify they fail.** Run (from `backend/`): `python3 -m pytest tests/test_a3_eval.py -k field_agreement -q`
Expected: FAIL — key absent.

- [ ] **Step 3: Implement the backend.** In `backend/app/main.py` `documents_stats`, before the `return`, add:

```python
    approved_fields = (db.query(ExtractedField.edited_by_user)
                       .join(Document, ExtractedField.document_id == Document.id)
                       .filter(Document.status == "approved").all())
    agreement = round(sum(1 for (e,) in approved_fields if not e) / len(approved_fields), 2) \
        if approved_fields else None
```

and add `"field_agreement_rate": agreement` to the returned dict. (`ExtractedField` is already imported in `main.py`.)

- [ ] **Step 4: Run the backend tests.** Run (from `backend/`): `python3 -m pytest tests/test_a3_eval.py -k field_agreement -q`
Expected: PASS.

- [ ] **Step 5: Add the frontend tile.** In `frontend/app/page.tsx`:
- Extend the `stats` state type (line ~13) to add `field_agreement_rate`:
```tsx
const [stats,setStats]=useState<{total:number,review_required:number,avg_processing_time:number|null,field_agreement_rate:number|null}|null>(null);
```
- Add a computed value next to `avgTime` (line ~85):
```tsx
 const agreement=stats?.field_agreement_rate!=null?`${Math.round(stats.field_agreement_rate*100)}%`:"—";
```
- Add a tile to the dashboard tiles array (line ~89), after the `Reviews` entry:
```tsx
["Fields accepted as-is",agreement,ShieldCheck]
```
(`ShieldCheck` is already imported.)

- [ ] **Step 6: Write the frontend test.** Create `frontend/app/field-agreement.test.tsx`:

```tsx
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import Home from "./page";

vi.mock("../lib/api", () => ({
  api: vi.fn(async (path: string) => {
    if (path === "/auth/me") return { role: "admin", email: "a@b.co" };
    if (path === "/schemas") return [];
    if (path.startsWith("/documents/stats")) return { total: 4, review_required: 1, avg_processing_time: 1, field_agreement_rate: 0.92 };
    if (path.startsWith("/documents")) return { items: [], total: 0 };
    return {};
  }),
  me: vi.fn(async () => ({ role: "admin", email: "a@b.co" })),
  pollDocument: vi.fn(), downloadFile: vi.fn(), TERMINAL: ["processed", "review_required", "error"],
}));

describe("field agreement tile", () => {
  beforeEach(() => vi.clearAllMocks());
  it("renders the 'Fields accepted as-is' tile from stats", async () => {
    render(<Home />);
    await waitFor(() => expect(screen.getByText("Fields accepted as-is")).toBeTruthy());
    expect(screen.getByText("92%")).toBeTruthy();
  });
});
```

- [ ] **Step 7: Run tests + build.** Run (from `frontend/`): `npx vitest run && npm run build` (expect 36 = 35 + 1 new; build succeeds). Run (from `backend/`): `python3 -m pytest -q` (expect 211 = 209 + 2 new).

- [ ] **Step 8: Commit.**

```bash
git add backend/app/main.py frontend/app/page.tsx backend/tests/test_a3_eval.py frontend/app/field-agreement.test.tsx
git commit -m "feat: field_agreement_rate in /documents/stats + 'Fields accepted as-is' tile"
```

---

### Task 6: Docs

**Files:**
- Modify: `docs/roadmap.md`, `docs/deployment.md`

- [ ] **Step 1: `docs/roadmap.md`.** In the "Revised execution order" note, mark **A3 ✅ Shipped (2026-08-16)** and set the next item to **A4 (B9 auto-approve/STP)**. Keep the rest intact.

- [ ] **Step 2: `docs/deployment.md`.** Add an `## A3 — accuracy measurement & confidence` note: `document.confidence` is now the min over required fields (a wrong required field can't be averaged away); run `python scripts/eval.py` (from `backend/`) for a correction-derived accuracy/calibration report (numbers are an **upper bound** — unaudited-but-approved fields count as correct); `/documents/stats` exposes `field_agreement_rate` (surfaced as the "Fields accepted as-is" tile); **a curated golden set (`--golden`) is a prerequisite before enabling B9 auto-approve**. No migration, no new deps/env.

- [ ] **Step 3: Commit.**

```bash
git add docs/roadmap.md docs/deployment.md
git commit -m "docs: A3 accuracy harness + confidence — roadmap A3 shipped + deployment note"
```

---

## Self-Review

**Spec coverage:** `document_confidence` (min-over-required) + pipeline wiring → Task 1. Pure harness math (Wilson, per-field, reliability, grounded-but-wrong, build_report) → Task 2. DB-sourced `correction_records`/`golden_records` + fixture format → Task 3. `scripts/eval.py` CLI with honest header → Task 4. `field_agreement_rate` + honestly-labeled tile → Task 5. Docs (incl. golden-set-is-a-B9-prerequisite) → Task 6. Guardrails: min-N `enough` flag (Tasks 2/4), Wilson lower bound (Task 2), grounded-but-wrong first-class (Task 2), upper-bound labeling (Tasks 2/4/5/6), read-only (no writes/threshold changes anywhere).

**Placeholder scan:** No TBD/TODO. Every code step has complete code. The golden fixture template is intentionally a format example with a placeholder `document_id: 0` (a documented template, not runnable data — curation is deferred per spec).

**Type consistency:** `EvalRecord(document_type, field_name, required, confidence, grounded, correct, source)` defined in Task 2, constructed identically in Task 3. `document_confidence(fields, schema_fields)` (Task 1) consumed by the pipeline. `field_agreement_rate` produced in Task 5 backend, typed + read in Task 5 frontend. `build_report` keys (`n/header/per_field/reliability/grounded_but_wrong`) produced in Task 2, consumed by the CLI in Task 4. Test counts cumulative: backend 195 → 211; frontend 35 → 36.
