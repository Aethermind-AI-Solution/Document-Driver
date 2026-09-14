# AP-IDP Demo Sprint — Week 1 Implementation Plan (W0 + W1 + W3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** De-risk the sprint's central bet (bounding boxes), stand up the demo dataset + golden set, make document confidence *honest* (calibrated against labels), and ship duplicate-invoice detection.

**Architecture:** Additive changes to the existing FastAPI backend. W0 is a throwaway spike + fixtures (no production code). W1 adds a calibration harness over the existing grounding-derived confidence and records a go/no-go on headlining a Straight-Through-Processing (STP) number. W3 adds a `fingerprint` column to `documents`, computes it in the pipeline after extraction, and flags duplicates through the existing `anomalies` path (which already drives `review_required` and is already serialized to the API).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, pytest. Matches the repo's existing patterns.

**Scope note:** W2 (document viewer / highlighting), W4 (ROI dashboard), W5 (OCR), and W6 (PII redaction) are a SEPARATE plan, written after Task 1's gate decides W2's architecture. This plan is independent of that gate.

## Global Constraints

- Work on branch `feat/ap-idp-demo-sprint` (already created). Commit locally; do NOT push without explicit approval.
- Every AI/vision/OCR feature sits behind a flag with a deterministic free fallback; dev runs $0. Paid paths run only in `demo-mode`.
- TDD per task. Keep the existing suites green (`cd backend && pytest -q` — currently ~105 passing). No regressions to shipped auth / org-isolation.
- All tenant-scoped DB rows carry `org_id` (fail-closed loader criteria). Any new query/row must respect org scoping.
- Alembic head is `0010_org_id_not_null`; new migrations chain from it.
- Run all commands from `backend/` unless stated otherwise.

---

### Task 1: W0 — Bounding-box feasibility spike (gate for W2)

Throwaway spike. Deliverable is a committed findings note with a go/no-go decision — NOT production code. This decides the architecture of the separate W2 plan.

**Files:**
- Create: `backend/scripts/spike_boxes.py` (throwaway; committed for reproducibility)
- Create: `docs/superpowers/specs/2026-09-13-w0-box-spike-findings.md` (the decision record)

**Interfaces:**
- Consumes: sample invoices from Task 2 (or 3 ad-hoc invoices if run first).
- Produces: the gate decision string (`model-boxes` | `ocr-box-text-match` | `text-span`) recorded in the findings doc; the W2 plan reads it.

- [ ] **Step 1: Write the spike script**

```python
# backend/scripts/spike_boxes.py
"""Throwaway: ask the vision model for per-field bounding boxes on sample
invoices, overlay them on the page image, and save PNGs for eyeballing.
Run with an OPENAI_API_KEY set (small one-off spend). NOT production code."""
import base64, json, os, sys
from pathlib import Path
from openai import OpenAI

SAMPLES = sys.argv[1:] or ["docs/demo/invoices/sample_01.png"]
FIELDS = ["vendor_name", "invoice_number", "invoice_date", "total"]
client = OpenAI()

def ask_boxes(img_path: str) -> dict:
    b64 = base64.b64encode(Path(img_path).read_bytes()).decode()
    prompt = ("Return STRICT JSON mapping each of these invoice fields to a "
              "bounding box as normalized [x0,y0,x1,y1] in 0..1 of the page, "
              f"plus the text value: {FIELDS}. "
              'Format: {"field": {"value": "...", "box": [x0,y0,x1,y1]}}.')
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]}],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)

def overlay(img_path: str, boxes: dict) -> None:
    from PIL import Image, ImageDraw
    im = Image.open(img_path).convert("RGB"); W, H = im.size
    d = ImageDraw.Draw(im)
    for name, entry in boxes.items():
        box = entry.get("box") if isinstance(entry, dict) else None
        if not (isinstance(box, list) and len(box) == 4):
            continue
        x0, y0, x1, y1 = [c * (W if i % 2 == 0 else H) for i, c in enumerate(box)]
        d.rectangle([x0, y0, x1, y1], outline="red", width=3); d.text((x0, y0 - 12), name, fill="red")
    out = Path(img_path).with_suffix(".boxes.png"); im.save(out); print("wrote", out)

if __name__ == "__main__":
    for s in SAMPLES:
        boxes = ask_boxes(s); print(s, json.dumps(boxes, indent=2)); overlay(s, boxes)
```

- [ ] **Step 2: Run the spike on 3 sample invoices**

Run: `cd backend && OPENAI_API_KEY=<key> python scripts/spike_boxes.py docs/demo/invoices/sample_01.png docs/demo/invoices/sample_02.png docs/demo/invoices/sample_03.png`
Expected: three `*.boxes.png` files written. Open them and eyeball whether each labeled box lands on the correct region.

- [ ] **Step 3: Record the gate decision**

Create `docs/superpowers/specs/2026-09-13-w0-box-spike-findings.md` with: the per-field hit/miss count across the 3 invoices, the overlay screenshots (or a description), and the decision:
- **≥ ~90% of boxes correct →** decision `model-boxes` (W2 uses vision boxes directly).
- **otherwise →** decision `ocr-box-text-match` (OCR engine word boxes + map value→box by text match); if OCR boxes are also impractical, decision `text-span` (highlight the source quote in an extracted-text pane, no image overlay).

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/spike_boxes.py docs/superpowers/specs/2026-09-13-w0-box-spike-findings.md
git commit -m "spike(w0): vision bounding-box feasibility + W2 gate decision"
```

---

### Task 2: W0 — Curated demo dataset + golden-set labels

**Files:**
- Create: `docs/demo/invoices/` (5–8 realistic sample invoices: PDF/PNG; include one deliberate near-duplicate of another, differing only by a trivial field)
- Create: `docs/demo/golden_set.json` (per-field ground-truth labels for the samples)
- Create: `backend/tests/fixtures/golden_set.json` (a small committed copy used by tests — 2 docs is enough)
- Create: `backend/app/goldens.py` (loader)
- Create: `backend/tests/test_w0_goldens.py`

**Interfaces:**
- Produces: `goldens.load_golden_set(path: str) -> list[dict]` where each item is
  `{"doc": "<filename>", "document_type": "invoice", "fields": {"<field_name>": "<true_value>", ...}}`.
  Consumed by Task 3 (calibration) and the W2/W4 plan.

- [ ] **Step 1: Assemble the sample invoices**

Place 5–8 invoices in `docs/demo/invoices/`. At least one pair must be near-duplicates (same vendor + invoice number + amount, e.g. `sample_02.png` and `sample_02_dup.png`) to drive the W3 demo beat. Also drop ~10 messy real-world public invoices under `docs/demo/invoices/wild/` for closer pre-testing (not labeled).

- [ ] **Step 2: Write the golden labels**

Create `docs/demo/golden_set.json` and the 2-doc test copy `backend/tests/fixtures/golden_set.json`:

```json
[
  {"doc": "sample_01.png", "document_type": "invoice",
   "fields": {"vendor_name": "Acme Supplies Pvt Ltd", "invoice_number": "INV-1042",
              "invoice_date": "2026-07-01", "total": "11800.00"}},
  {"doc": "sample_02.png", "document_type": "invoice",
   "fields": {"vendor_name": "Globex Traders", "invoice_number": "G-5581",
              "invoice_date": "2026-07-03", "total": "4720.00"}}
]
```

- [ ] **Step 3: Write the failing test**

```python
# backend/tests/test_w0_goldens.py
from app.goldens import load_golden_set

def test_load_golden_set_returns_labeled_docs():
    items = load_golden_set("tests/fixtures/golden_set.json")
    assert len(items) >= 2
    first = items[0]
    assert set(first) == {"doc", "document_type", "fields"}
    assert isinstance(first["fields"], dict) and first["fields"]
```

- [ ] **Step 2b: Run test to verify it fails**

Run: `pytest tests/test_w0_goldens.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.goldens'`.

- [ ] **Step 4: Implement the loader**

```python
# backend/app/goldens.py
import json
from pathlib import Path

def load_golden_set(path: str) -> list[dict]:
    """Load per-field ground-truth labels for calibration/eval."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, list):
        raise ValueError("golden set must be a JSON array")
    for item in data:
        if not {"doc", "document_type", "fields"} <= set(item):
            raise ValueError(f"golden item missing keys: {item}")
    return data
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_w0_goldens.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add docs/demo backend/app/goldens.py backend/tests/fixtures/golden_set.json backend/tests/test_w0_goldens.py
git commit -m "feat(w0): demo dataset + golden-set loader"
```

---

### Task 3: W1 — Confidence calibration harness + STP go/no-go

The pipeline already derives confidence from grounding (`services._ground`, `services.document_confidence`). W1's job is to prove — against labels — whether that confidence *separates correct from wrong*, and to decide whether the demo may headline a precise STP %.

**Files:**
- Create: `backend/app/calibration.py`
- Create: `backend/tests/test_w1_calibration.py`
- Create: `backend/scripts/calibrate_confidence.py` (CLI that prints the report over the full golden set)
- Create: `docs/superpowers/specs/2026-09-13-w1-stp-decision.md` (the go/no-go record)

**Interfaces:**
- Consumes: `goldens.load_golden_set` (Task 2).
- Produces: `calibration.separation(records: list[dict]) -> dict` where each record is
  `{"confidence": float, "correct": bool}` and the return is
  `{"n": int, "mean_correct": float | None, "mean_wrong": float | None, "margin": float | None}`.
  `margin = mean_correct - mean_wrong` (None if either class is empty).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_w1_calibration.py
from app.calibration import separation

def test_separation_reports_margin_between_correct_and_wrong():
    records = [
        {"confidence": 0.95, "correct": True},
        {"confidence": 0.90, "correct": True},
        {"confidence": 0.40, "correct": False},
        {"confidence": 0.55, "correct": False},
    ]
    out = separation(records)
    assert out["n"] == 4
    assert out["mean_correct"] == 0.925
    assert out["mean_wrong"] == 0.475
    assert round(out["margin"], 3) == 0.45

def test_separation_handles_single_class():
    out = separation([{"confidence": 0.9, "correct": True}])
    assert out["mean_wrong"] is None and out["margin"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_w1_calibration.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.calibration'`.

- [ ] **Step 3: Implement the calibration module**

```python
# backend/app/calibration.py
"""Does grounding-derived confidence separate correct from wrong extractions?
Input records are {"confidence": float, "correct": bool}; correctness is decided
by the caller against the golden set."""

def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 3) if xs else None

def separation(records: list[dict]) -> dict:
    correct = [r["confidence"] for r in records if r["correct"]]
    wrong = [r["confidence"] for r in records if not r["correct"]]
    mc, mw = _mean(correct), _mean(wrong)
    margin = round(mc - mw, 3) if (mc is not None and mw is not None) else None
    return {"n": len(records), "mean_correct": mc, "mean_wrong": mw, "margin": margin}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_w1_calibration.py -v`
Expected: PASS.

- [ ] **Step 5: Write the CLI harness**

```python
# backend/scripts/calibrate_confidence.py
"""Run extraction over the golden set and report confidence separation.
Runs in fallback mode ($0) by default; set OPENAI_API_KEY for the AI path."""
import sys
from app import services, goldens, calibration

def value_matches(true_v: str, got_v: str | None) -> bool:
    from app.services import _norm
    return got_v is not None and _norm(str(true_v)) == _norm(str(got_v))

def main(golden_path: str, invoices_dir: str) -> None:
    items = goldens.load_golden_set(golden_path)
    records = []
    for item in items:
        schema = {"fields": [{"name": k} for k in item["fields"]]}
        text = services.extract_text(f"{invoices_dir}/{item['doc']}")
        data = services.fallback_extract(text, schema["fields"])
        grounded = services._ground_fields(data, schema["fields"], text)
        by_name = {g["field_name"]: g for g in grounded}
        for fname, true_v in item["fields"].items():
            g = by_name.get(fname, {})
            records.append({"confidence": g.get("confidence", 0.0),
                            "correct": value_matches(true_v, g.get("field_value"))})
    print(calibration.separation(records))

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
```

- [ ] **Step 6: Run the harness and record the decision**

Run: `cd backend && python scripts/calibrate_confidence.py ../docs/demo/golden_set.json ../docs/demo/invoices`
Then create `docs/superpowers/specs/2026-09-13-w1-stp-decision.md` recording the printed `margin` and the decision:
- **margin ≥ 0.2 (correct clearly > wrong) →** STP % may be headlined in the W4 ROI dashboard.
- **otherwise →** per the spec kill criterion, the dashboard shows "exceptions flagged" instead of a precise STP %, and W1 follow-up (verifier/self-consistency) moves into the W2/W4 plan.

- [ ] **Step 7: Commit**

```bash
git add backend/app/calibration.py backend/tests/test_w1_calibration.py backend/scripts/calibrate_confidence.py docs/superpowers/specs/2026-09-13-w1-stp-decision.md
git commit -m "feat(w1): confidence calibration harness + STP go/no-go decision"
```

---

### Task 4: W3 — `fingerprint` column + migration

**Files:**
- Modify: `backend/app/models.py` (add `fingerprint` to `Document`, after `auto_approved` at line 45)
- Create: `backend/alembic/versions/0011_document_fingerprint.py`
- Create: `backend/tests/test_w3_migration.py`

**Interfaces:**
- Produces: `Document.fingerprint: Mapped[str | None]` (nullable, indexed). Consumed by Tasks 5–6.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_w3_migration.py
from app.models import Document

def test_document_has_fingerprint_column():
    assert "fingerprint" in Document.__table__.columns
    assert Document.__table__.columns["fingerprint"].nullable is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_w3_migration.py -v`
Expected: FAIL with `KeyError: 'fingerprint'`.

- [ ] **Step 3: Add the column to the model**

In `backend/app/models.py`, immediately after line 45 (`auto_approved: ...`), add:

```python
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_w3_migration.py -v`
Expected: PASS (the test DB is created from the model metadata via `Base.metadata.create_all`).

- [ ] **Step 5: Write the Alembic migration**

```python
# backend/alembic/versions/0011_document_fingerprint.py
"""document fingerprint for duplicate detection

Revision ID: 0011_document_fingerprint
Revises: 0010_org_id_not_null
Create Date: 2026-09-13 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0011_document_fingerprint"
down_revision: Union[str, None] = "0010_org_id_not_null"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("documents", schema=None) as b:
        b.add_column(sa.Column("fingerprint", sa.String(length=64), nullable=True))
        b.create_index("ix_documents_fingerprint", ["fingerprint"])


def downgrade() -> None:
    with op.batch_alter_table("documents", schema=None) as b:
        b.drop_index("ix_documents_fingerprint")
        b.drop_column("fingerprint")
```

- [ ] **Step 6: Verify the migration applies on a scratch SQLite DB**

Run: `cd backend && DATABASE_URL="sqlite:///$(pwd)/_scratch_fp.db" alembic upgrade head && rm -f _scratch_fp.db`
Expected: ends at `0011_document_fingerprint` with no error.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models.py backend/alembic/versions/0011_document_fingerprint.py backend/tests/test_w3_migration.py
git commit -m "feat(w3): document fingerprint column + migration 0011"
```

---

### Task 5: W3 — Fingerprint + duplicate-detection services

**Files:**
- Modify: `backend/app/services.py` (add two functions near `document_confidence`, ~line 156)
- Create: `backend/tests/test_w3_dedup.py`

**Interfaces:**
- Consumes: `Document.fingerprint` (Task 4); `_norm` (existing, `services.py:138`).
- Produces:
  - `services.compute_fingerprint(fields: list[dict]) -> str | None` — stable SHA-256 hex of
    normalized (vendor_name, invoice_number, total, invoice_date); `None` if invoice_number is missing.
  - `services.find_duplicate(db, document) -> Document | None` — earliest other doc in the same org
    with the same non-null fingerprint, or `None`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_w3_dedup.py
from app import services
from app.models import Document

def _fields(vendor, num, total, date):
    return [
        {"field_name": "vendor_name", "field_value": vendor},
        {"field_name": "invoice_number", "field_value": num},
        {"field_name": "total", "field_value": total},
        {"field_name": "invoice_date", "field_value": date},
    ]

def test_fingerprint_stable_and_ignores_case_space():
    a = services.compute_fingerprint(_fields("Acme  Supplies", "INV-1042", "11800.00", "2026-07-01"))
    b = services.compute_fingerprint(_fields("acme supplies", "inv-1042", "11800.00", "2026-07-01"))
    assert a and a == b

def test_fingerprint_none_without_invoice_number():
    assert services.compute_fingerprint(_fields("Acme", None, "1", "2026-07-01")) is None

def test_find_duplicate_matches_same_org(db_session):
    from app.context import set_current_org
    set_current_org(1)
    fp = services.compute_fingerprint(_fields("Acme", "INV-1042", "11800.00", "2026-07-01"))
    first = Document(filename="a.png", document_type="invoice", stored_path="a", org_id=1, fingerprint=fp)
    db_session.add(first); db_session.commit()
    second = Document(filename="b.png", document_type="invoice", stored_path="b", org_id=1, fingerprint=fp)
    db_session.add(second); db_session.commit()
    dup = services.find_duplicate(db_session, second)
    assert dup is not None and dup.id == first.id

def test_find_duplicate_none_when_unique(db_session):
    from app.context import set_current_org
    set_current_org(1)
    doc = Document(filename="c.png", document_type="invoice", stored_path="c", org_id=1,
                   fingerprint=services.compute_fingerprint(_fields("X", "UNIQ-1", "5", "2026-01-01")))
    db_session.add(doc); db_session.commit()
    assert services.find_duplicate(db_session, doc) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_w3_dedup.py -v`
Expected: FAIL with `AttributeError: module 'app.services' has no attribute 'compute_fingerprint'`.

- [ ] **Step 3: Implement the services**

Add to `backend/app/services.py` (after `document_confidence`, before `_ground`; add `import hashlib` at the top if absent):

```python
def compute_fingerprint(fields: list[dict]) -> str | None:
    """Stable hash of the invoice identity (vendor+number+total+date). Returns
    None when the invoice number is missing (can't reliably de-dupe without it)."""
    by_name = {f["field_name"]: f.get("field_value") for f in fields}
    number = _norm(by_name.get("invoice_number"))
    if not number:
        return None
    parts = [_norm(by_name.get("vendor_name")), number,
             _norm(by_name.get("total")), _norm(by_name.get("invoice_date"))]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def find_duplicate(db: Session, document) -> "Document | None":
    """Earliest OTHER document in the same org sharing this fingerprint. Org
    scoping is enforced by the fail-closed loader criteria already in place."""
    from .models import Document as _Doc
    if not document.fingerprint:
        return None
    return (db.query(_Doc)
            .filter(_Doc.fingerprint == document.fingerprint, _Doc.id != document.id)
            .order_by(_Doc.id.asc()).first())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_w3_dedup.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services.py backend/tests/test_w3_dedup.py
git commit -m "feat(w3): fingerprint + duplicate-detection services"
```

---

### Task 6: W3 — Wire duplicate detection into the pipeline

Compute the fingerprint after fields are committed, and if a duplicate exists add it to `anomalies` (which already forces `review_required` and is already serialized by `main.serialize`).

**Files:**
- Modify: `backend/app/agents/pipeline.py` (inside `run_pipeline`, after the fields loop and before `document.confidence = ...`, around line 60)
- Create: `backend/tests/test_w3_pipeline.py`

**Interfaces:**
- Consumes: `services.compute_fingerprint`, `services.find_duplicate` (Task 5).
- Produces: a duplicate anomaly dict in `document.anomalies`:
  `{"type": "duplicate_invoice", "message": "Possible duplicate of <filename>", "duplicate_of": <id>}`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_w3_pipeline.py
from app import services
from app.agents import pipeline
from app.models import Document

def test_pipeline_flags_duplicate(db_session, monkeypatch):
    from app.context import set_current_org
    set_current_org(1)
    fields = [{"field_name": "invoice_number", "field_value": "INV-1042"},
              {"field_name": "total", "field_value": "100.00"}]
    fp = services.compute_fingerprint(fields)
    prior = Document(filename="original.png", document_type="invoice", stored_path="o",
                     org_id=1, fingerprint=fp)
    db_session.add(prior); db_session.commit()

    current = Document(filename="copy.png", document_type="invoice", stored_path="c",
                       org_id=1, fingerprint=fp)
    db_session.add(current); db_session.commit()

    anomalies = pipeline.detect_duplicate(db_session, current)
    assert anomalies == [{"type": "duplicate_invoice",
                          "message": "Possible duplicate of original.png",
                          "duplicate_of": prior.id}]

def test_pipeline_no_duplicate_returns_empty(db_session):
    from app.context import set_current_org
    set_current_org(1)
    doc = Document(filename="solo.png", document_type="invoice", stored_path="s",
                   org_id=1, fingerprint="unique-fp")
    db_session.add(doc); db_session.commit()
    assert pipeline.detect_duplicate(db_session, doc) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_w3_pipeline.py -v`
Expected: FAIL with `AttributeError: module 'app.agents.pipeline' has no attribute 'detect_duplicate'`.

- [ ] **Step 3: Implement `detect_duplicate` and call it in `run_pipeline`**

Add this helper to `backend/app/agents/pipeline.py` (module level, after the imports):

```python
def detect_duplicate(db: Session, document: Document) -> list[dict]:
    """Return a duplicate anomaly (as a one-item list) if another doc in the org
    shares this fingerprint, else an empty list."""
    dup = services.find_duplicate(db, document)
    if dup is None:
        return []
    return [{"type": "duplicate_invoice",
             "message": f"Possible duplicate of {dup.filename}",
             "duplicate_of": dup.id}]
```

Then in `run_pipeline`, replace the fields-commit + confidence block. The current code (around lines 53-64) is:

```python
        db.query(ExtractedField).filter_by(document_id=document.id).delete()
        for f in ctx.fields:
            db.add(ExtractedField(document_id=document.id, original_value=f["field_value"],
                                  org_id=document.org_id, **f))
        document.pipeline_trace = [asdict(s) for s in ctx.trace]
        document.anomalies = ctx.anomalies or None
        document.confidence = services.document_confidence(ctx.fields, ctx.schema["fields"])
```

Change it to compute the fingerprint and merge duplicate anomalies:

```python
        db.query(ExtractedField).filter_by(document_id=document.id).delete()
        for f in ctx.fields:
            db.add(ExtractedField(document_id=document.id, original_value=f["field_value"],
                                  org_id=document.org_id, **f))
        document.fingerprint = services.compute_fingerprint(ctx.fields)
        dup_anomalies = detect_duplicate(db, document)
        document.pipeline_trace = [asdict(s) for s in ctx.trace]
        document.anomalies = (ctx.anomalies or []) + dup_anomalies or None
        document.confidence = services.document_confidence(ctx.fields, ctx.schema["fields"])
```

(`document.review_required` on the next line already becomes `True` when `anomalies` is truthy — no change needed.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_w3_pipeline.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full backend suite for regressions**

Run: `cd backend && pytest -q`
Expected: all prior tests still pass, plus the new W0/W1/W3 tests.

- [ ] **Step 6: Commit**

```bash
git add backend/app/agents/pipeline.py backend/tests/test_w3_pipeline.py
git commit -m "feat(w3): flag duplicate invoices via pipeline anomalies"
```

---

## Self-Review

**Spec coverage (Week-1 slice of the sprint spec):**
- W0 box spike + gate decision → Task 1. ✅
- W0 demo dataset + golden set + golden-set check → Task 2. ✅
- W1 honest confidence (grounding-verifier over self-consistency) + calibration + STP kill-criterion → Task 3. ✅
- W3 duplicate detection (fingerprint, org-scoped, surfaced via anomalies) → Tasks 4–6. ✅
- W2/W4/W5/W6 → explicitly deferred to the follow-up plan (gated on Task 1). Noted in the header.

**Placeholder scan:** No "TBD"/"handle edge cases"/"write tests for the above" — every code step shows complete code and every command shows expected output.

**Type consistency:** `compute_fingerprint(fields) -> str | None` and `find_duplicate(db, document) -> Document | None` are defined in Task 5 and consumed with those exact names/shapes in Task 6; `detect_duplicate` returns the exact anomaly dict asserted in Task 6's test; `separation(records) -> dict` keys (`n/mean_correct/mean_wrong/margin`) match between Task 3's test and implementation; `load_golden_set` item shape matches between Tasks 2 and 3.
