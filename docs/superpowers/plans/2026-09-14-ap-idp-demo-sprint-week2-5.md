# AP-IDP Demo Sprint — W2/W5/W4/W6 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the document-viewer + click-to-highlight review (W2), scanned-doc OCR robustness (W5), a buyer-editable ROI dashboard (W4), and a PII-redaction toggle + security posture (W6) — the rest of the demo-ready feature set.

**Architecture:** A shared OCR foundation (`app/ocr.py`) built on **AWS Textract via the already-present `boto3`** (no new dependency) provides, from one call, both word-level bounding boxes (W2) and full page text (W5). W2 maps extracted field values to Textract word boxes and renders them over the page image in a split-screen React viewer. W5 enriches thin/absent PDF text layers with OCR text before extraction. W4 adds a metrics endpoint + a dashboard page whose $-saved math is driven by buyer-editable inputs. W6 masks schema fields flagged PII behind a reviewer toggle. Everything AI/OCR is behind flags with deterministic free fallbacks (`OCR_BACKEND=none` → empty boxes/text; the app degrades to today's text-only review).

**Tech Stack:** Backend — Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, PyMuPDF (`fitz`), boto3 (Textract), pytest. Frontend — Next.js 15 (app router), React 19, TypeScript, Tailwind, lucide-react, vitest + @testing-library/react (jsdom).

**Decision (from the W0 gate, chosen by the user):** W2 boxes come from an OCR engine (Textract), unified with W5 — NOT vision-model coordinates.

## Global Constraints

- Work on a fresh branch off `main` after PR #10 merges: `git checkout main && git pull && git checkout -b feat/ap-idp-demo-w2-w6`. Commit locally; do NOT push without explicit approval (master = prod).
- Every OCR/AI feature behind a flag with a deterministic free fallback; dev runs $0. Paid Textract runs only when `OCR_BACKEND=textract` (set in `demo-mode`).
- OCR must be **fail-soft**: any Textract error returns empty text/boxes and logs a warning — it must NEVER break the pipeline or an endpoint.
- TDD per task. Backend test cmd: `cd backend && python3 -m pytest -q` (baseline after PR #10: 276 passed). Frontend test cmd: `cd frontend && npm test` (baseline: 26 passed). Keep both green; no regressions to auth / org-isolation.
- All tenant-scoped rows carry `org_id`; new queries respect the fail-closed loader criteria in `app/database.py`.
- Alembic head after PR #10 is `0011_document_fingerprint`; new migrations chain from it.
- Backend commands run from `backend/`, frontend from `frontend/`.
- Textract calls must never receive AWS creds in code — boto3 reads them from the standard env (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`).

---

## Phase A — Shared OCR foundation

### Task A1: `app/ocr.py` — Textract word boxes + text (fail-soft, flagged)

**Files:**
- Modify: `backend/app/config.py` (add flags after `S3_REGION`, ~line 30)
- Create: `backend/app/ocr.py`
- Create: `backend/tests/test_ocr.py`

**Interfaces:**
- Produces: `ocr.ocr_image(data: bytes) -> dict` returning `{"text": str, "words": list[dict]}`, each word `{"text": str, "box": [x0, y0, x1, y1]}` with box normalized to 0..1 as `[left, top, right, bottom]`. Returns `{"text": "", "words": []}` when `OCR_BACKEND != "textract"` or on any error.
- Produces: `ocr._textract_client()` — factory tests monkeypatch.

- [ ] **Step 1: Add config flags**

In `backend/app/config.py` after the `S3_REGION` line, add:

```python
OCR_BACKEND = os.getenv("OCR_BACKEND", "none")  # "none" | "textract"
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
OCR_TEXT_MIN_CHARS = int(os.getenv("OCR_TEXT_MIN_CHARS", "40"))  # below this, a page is "thin" and gets OCR'd
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_ocr.py
from app import ocr, config

class _FakeTextract:
    def detect_document_text(self, Document):
        return {"Blocks": [
            {"BlockType": "WORD", "Text": "Acme",
             "Geometry": {"BoundingBox": {"Left": 0.1, "Top": 0.2, "Width": 0.1, "Height": 0.05}}},
            {"BlockType": "LINE", "Text": "ignored line"},
            {"BlockType": "WORD", "Text": "Supplies",
             "Geometry": {"BoundingBox": {"Left": 0.21, "Top": 0.2, "Width": 0.15, "Height": 0.05}}},
        ]}

def test_ocr_image_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(config, "OCR_BACKEND", "none")
    assert ocr.ocr_image(b"x") == {"text": "", "words": []}

def test_ocr_image_parses_words_and_text(monkeypatch):
    monkeypatch.setattr(config, "OCR_BACKEND", "textract")
    monkeypatch.setattr(ocr, "_textract_client", lambda: _FakeTextract())
    out = ocr.ocr_image(b"imgbytes")
    assert out["text"] == "Acme Supplies"
    assert out["words"][0] == {"text": "Acme", "box": [0.1, 0.2, 0.2, 0.25]}
    assert out["words"][1]["text"] == "Supplies"

def test_ocr_image_failsoft_on_error(monkeypatch):
    monkeypatch.setattr(config, "OCR_BACKEND", "textract")
    def _boom(): raise RuntimeError("aws down")
    monkeypatch.setattr(ocr, "_textract_client", _boom)
    assert ocr.ocr_image(b"x") == {"text": "", "words": []}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_ocr.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ocr'`.

- [ ] **Step 4: Implement `app/ocr.py`**

```python
# backend/app/ocr.py
"""OCR via AWS Textract (boto3, already a dependency). Fail-soft: any error or a
disabled backend returns empty text/boxes so the pipeline never breaks. Boxes are
normalized [left, top, right, bottom] in 0..1."""
import logging
from . import config

_log = logging.getLogger("aethermind")


def _textract_client():
    import boto3
    return boto3.client("textract", region_name=config.AWS_REGION)


def ocr_image(data: bytes) -> dict:
    if config.OCR_BACKEND != "textract":
        return {"text": "", "words": []}
    try:
        resp = _textract_client().detect_document_text(Document={"Bytes": data})
    except Exception:
        _log.warning("ocr failed; returning empty result", exc_info=True)
        return {"text": "", "words": []}
    words = []
    for b in resp.get("Blocks", []):
        if b.get("BlockType") != "WORD":
            continue
        bb = b.get("Geometry", {}).get("BoundingBox")
        if not bb:
            continue
        x0, y0 = bb["Left"], bb["Top"]
        words.append({"text": b.get("Text", ""),
                      "box": [round(x0, 4), round(y0, 4),
                              round(x0 + bb["Width"], 4), round(y0 + bb["Height"], 4)]})
    return {"text": " ".join(w["text"] for w in words), "words": words}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_ocr.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/config.py backend/app/ocr.py backend/tests/test_ocr.py
git commit -m "feat(ocr): Textract word-box + text foundation (fail-soft, flagged)"
```

### Task A2: `render_png` — rasterize a PDF page for OCR + display

**Files:**
- Modify: `backend/app/agents/pages.py` (add function at module end)
- Create: `backend/tests/test_render_png.py`

**Interfaces:**
- Produces: `pages.render_png(pdf_bytes: bytes, dpi: int = 150) -> bytes` — first page of `pdf_bytes` as PNG; if the input isn't a PDF (already an image), returns it unchanged.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_render_png.py
import fitz
from app.agents.pages import render_png

def _one_page_pdf() -> bytes:
    doc = fitz.open(); doc.new_page(); data = doc.tobytes(); doc.close(); return data

def test_render_png_of_pdf_returns_png():
    out = render_png(_one_page_pdf())
    assert out[:8] == b"\x89PNG\r\n\x1a\n"

def test_render_png_passthrough_for_non_pdf():
    png = b"\x89PNG\r\n\x1a\nfake"
    assert render_png(png) == png
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_render_png.py -v`
Expected: FAIL with `ImportError: cannot import name 'render_png'`.

- [ ] **Step 3: Implement `render_png`**

Add to `backend/app/agents/pages.py` (module end):

```python
def render_png(pdf_bytes: bytes, dpi: int = 150) -> bytes:
    """First page of a PDF as PNG bytes. Non-PDF input (already an image) is
    returned unchanged so callers can pass either."""
    if pdf_bytes[:5] != b"%PDF-":
        return pdf_bytes
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        pix = doc[0].get_pixmap(dpi=dpi)
        return pix.tobytes("png")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_render_png.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/pages.py backend/tests/test_render_png.py
git commit -m "feat(ocr): render_png helper to rasterize a PDF page"
```

---

## Phase B — W5: scanned-doc OCR text fallback

### Task B1: Enrich thin-text pages with OCR before extraction

When a page's embedded text layer is thin (scanned/photographed), OCR the rasterized page and use that text — so extraction + grounding work on scans. Behind `OCR_BACKEND`.

**Files:**
- Modify: `backend/app/ocr.py` (add `enrich_pages`)
- Modify: `backend/app/agents/pipeline.py` (call it in `run_pipeline` after `ctx.pages = split_pages(...)`, ~line 41)
- Create: `backend/tests/test_w5_enrich.py`

**Interfaces:**
- Consumes: `ocr.ocr_image` (A1), `pages.render_png` (A2). Page dicts are `{"index": int, "pdf_bytes": bytes, "text": str}` (from `split_pages`).
- Produces: `ocr.enrich_pages(pages: list[dict]) -> list[dict]` — for each page whose `text` is shorter than `config.OCR_TEXT_MIN_CHARS`, replaces `text` with OCR text of `render_png(page["pdf_bytes"])` (only if OCR yields more text); otherwise leaves it. Returns the same list. No-op when `OCR_BACKEND != "textract"`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_w5_enrich.py
from app import ocr, config
from app.agents import pages as pages_mod

def test_enrich_pages_fills_thin_text(monkeypatch):
    monkeypatch.setattr(config, "OCR_BACKEND", "textract")
    monkeypatch.setattr(config, "OCR_TEXT_MIN_CHARS", 40)
    monkeypatch.setattr(pages_mod, "render_png", lambda b, dpi=150: b"png")
    monkeypatch.setattr(ocr, "render_png", lambda b, dpi=150: b"png", raising=False)
    monkeypatch.setattr(ocr, "ocr_image", lambda data: {"text": "OCR RECOVERED TEXT " * 5, "words": []})
    pages = [{"index": 0, "pdf_bytes": b"%PDF-...", "text": "short"}]
    out = ocr.enrich_pages(pages)
    assert out[0]["text"].startswith("OCR RECOVERED TEXT")

def test_enrich_pages_keeps_rich_text(monkeypatch):
    monkeypatch.setattr(config, "OCR_BACKEND", "textract")
    monkeypatch.setattr(config, "OCR_TEXT_MIN_CHARS", 40)
    rich = "x" * 200
    pages = [{"index": 0, "pdf_bytes": b"%PDF-...", "text": rich}]
    assert ocr.enrich_pages(pages)[0]["text"] == rich

def test_enrich_pages_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "OCR_BACKEND", "none")
    pages = [{"index": 0, "pdf_bytes": b"x", "text": ""}]
    assert ocr.enrich_pages(pages)[0]["text"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_w5_enrich.py -v`
Expected: FAIL with `AttributeError: module 'app.ocr' has no attribute 'enrich_pages'`.

- [ ] **Step 3: Implement `enrich_pages`**

Add to `backend/app/ocr.py` (import `render_png` at top: `from .agents.pages import render_png`):

```python
def enrich_pages(pages: list[dict]) -> list[dict]:
    """Replace thin/absent PDF text layers with OCR text so scanned docs extract.
    No-op unless OCR is enabled. Only overwrites when OCR yields more text."""
    if config.OCR_BACKEND != "textract":
        return pages
    for p in pages:
        if len((p.get("text") or "").strip()) >= config.OCR_TEXT_MIN_CHARS:
            continue
        recovered = ocr_image(render_png(p["pdf_bytes"])).get("text", "")
        if len(recovered.strip()) > len((p.get("text") or "").strip()):
            p["text"] = recovered
    return pages
```

Note: `from .agents.pages import render_png` at the top of `ocr.py`. If a circular import arises (pages.py imports services, not ocr — should be safe), import lazily inside `enrich_pages` instead.

- [ ] **Step 4: Wire into the pipeline**

In `backend/app/agents/pipeline.py`, add `from .. import ocr` to the imports, and in `run_pipeline` right after `ctx.pages = split_pages(data, suffix)` add:

```python
        ctx.pages = ocr.enrich_pages(ctx.pages)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python3 -m pytest tests/test_w5_enrich.py tests/test_phase2_pipeline.py -v`
Expected: PASS (new W5 tests + existing pipeline tests unaffected — `OCR_BACKEND` defaults to `none`, so `enrich_pages` is a no-op in the suite).

- [ ] **Step 6: Commit**

```bash
git add backend/app/ocr.py backend/app/agents/pipeline.py backend/tests/test_w5_enrich.py
git commit -m "feat(w5): OCR-enrich thin-text pages before extraction (scanned-doc robustness)"
```

---

## Phase C — W2: field boxes, image endpoint, split-screen viewer

### Task C1: `box` column on `ExtractedField` + migration 0012

**Files:**
- Modify: `backend/app/models.py` (add to `ExtractedField`, after `grounded` ~line 60)
- Create: `backend/alembic/versions/0012_extracted_field_box.py`
- Create: `backend/tests/test_w2_migration.py`

**Interfaces:**
- Produces: `ExtractedField.box: Mapped[list | None]` — nullable JSON, a `[x0,y0,x1,y1]` normalized box or null.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_w2_migration.py
from app.models import ExtractedField

def test_extracted_field_has_box_column():
    col = ExtractedField.__table__.columns.get("box")
    assert col is not None and col.nullable is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_w2_migration.py -v`
Expected: FAIL (`box` not in columns).

- [ ] **Step 3: Add the column**

In `backend/app/models.py`, in `ExtractedField` after the `grounded` line, add:

```python
    box: Mapped[list | None] = mapped_column(JSON, nullable=True)
```

(`JSON` is already imported in models.py — it's used by `Document.pipeline_trace`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_w2_migration.py -v`
Expected: PASS.

- [ ] **Step 5: Write the migration**

```python
# backend/alembic/versions/0012_extracted_field_box.py
"""extracted_field bounding box for click-to-highlight review

Revision ID: 0012_extracted_field_box
Revises: 0011_document_fingerprint
Create Date: 2026-09-14 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0012_extracted_field_box"
down_revision: Union[str, None] = "0011_document_fingerprint"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("extracted_fields", schema=None) as b:
        b.add_column(sa.Column("box", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("extracted_fields", schema=None) as b:
        b.drop_column("box")
```

- [ ] **Step 6: Verify the migration applies**

Run: `cd backend && DATABASE_URL="sqlite:///$(pwd)/_scratch_box.db" python3 -m alembic upgrade head && rm -f _scratch_box.db`
Expected: ends at `0012_extracted_field_box`, no error.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models.py backend/alembic/versions/0012_extracted_field_box.py backend/tests/test_w2_migration.py
git commit -m "feat(w2): extracted_field.box column + migration 0012"
```

### Task C2: `app/boxes.py` — map field values to word boxes

**Files:**
- Create: `backend/app/boxes.py`
- Create: `backend/tests/test_boxes.py`

**Interfaces:**
- Consumes: `services._norm` (existing).
- Produces: `boxes.map_field_boxes(fields: list[dict], words: list[dict]) -> dict[str, list[float]]` — for each field with a non-empty `field_value`, the union `[x0,y0,x1,y1]` of the word boxes whose normalized text is a token of the normalized field value; fields with no match are omitted. `words` are `{"text","box"}` from `ocr.ocr_image`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_boxes.py
from app.boxes import map_field_boxes

WORDS = [
    {"text": "Acme", "box": [0.10, 0.20, 0.20, 0.25]},
    {"text": "Supplies", "box": [0.21, 0.20, 0.36, 0.25]},
    {"text": "INV-1042", "box": [0.60, 0.10, 0.75, 0.14]},
]

def test_maps_multiword_value_to_union_box():
    fields = [{"field_name": "vendor_name", "field_value": "Acme Supplies"}]
    out = map_field_boxes(fields, WORDS)
    assert out["vendor_name"] == [0.10, 0.20, 0.36, 0.25]

def test_maps_single_token_value():
    fields = [{"field_name": "invoice_number", "field_value": "INV-1042"}]
    assert map_field_boxes(fields, WORDS)["invoice_number"] == [0.60, 0.10, 0.75, 0.14]

def test_omits_unmatched_and_empty():
    fields = [{"field_name": "po_number", "field_value": "PO-999"},
              {"field_name": "notes", "field_value": None}]
    assert map_field_boxes(fields, WORDS) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_boxes.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.boxes'`).

- [ ] **Step 3: Implement `app/boxes.py`**

```python
# backend/app/boxes.py
"""Map extracted field values to Textract word boxes by token match. Boxes are
normalized [x0,y0,x1,y1]; a field's box is the union of the word boxes whose text
is one of the value's tokens."""
import re
from .services import _norm


def _tokens(value: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", _norm(value)) if t}


def _union(bs: list[list[float]]) -> list[float]:
    return [min(b[0] for b in bs), min(b[1] for b in bs),
            max(b[2] for b in bs), max(b[3] for b in bs)]


def map_field_boxes(fields: list[dict], words: list[dict]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for f in fields:
        val = f.get("field_value")
        if not val or not str(val).strip():
            continue
        toks = _tokens(str(val))
        if not toks:
            continue
        matched = [w["box"] for w in words if _norm(w["text"]) in toks and w.get("box")]
        if matched:
            out[f["field_name"]] = [round(c, 4) for c in _union(matched)]
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_boxes.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/boxes.py backend/tests/test_boxes.py
git commit -m "feat(w2): map field values to word boxes"
```

### Task C3: Store field boxes in the pipeline + serialize them

**Files:**
- Modify: `backend/app/agents/pipeline.py` (`run_pipeline`, in the fields-commit block ~lines 63-71)
- Modify: `backend/app/main.py` (`serialize`, add `box` to each field dict ~line 128)
- Create: `backend/tests/test_w2_pipeline_boxes.py`

**Interfaces:**
- Consumes: `ocr.ocr_image` (A1), `pages.render_png` (A2), `boxes.map_field_boxes` (C2).
- Produces: each committed `ExtractedField.box` set from page-0 OCR when `OCR_BACKEND=textract`; `serialize` emits `"box"` per field.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_w2_pipeline_boxes.py
from app.agents import pipeline

def test_compute_field_boxes_maps_from_first_page(monkeypatch):
    from app import ocr, boxes
    monkeypatch.setattr(ocr, "ocr_image", lambda data: {"text": "", "words": [
        {"text": "Acme", "box": [0.1, 0.2, 0.2, 0.25]}]})
    monkeypatch.setattr(pipeline, "render_png", lambda b, dpi=150: b"png", raising=False)
    fields = [{"field_name": "vendor_name", "field_value": "Acme"}]
    pages = [{"index": 0, "pdf_bytes": b"%PDF-", "text": ""}]
    result = pipeline.compute_field_boxes(fields, pages)
    assert result == {"vendor_name": [0.1, 0.2, 0.2, 0.25]}

def test_compute_field_boxes_empty_without_pages():
    assert pipeline.compute_field_boxes([{"field_name": "x", "field_value": "y"}], []) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_w2_pipeline_boxes.py -v`
Expected: FAIL (`compute_field_boxes` missing).

- [ ] **Step 3: Implement the helper and wire it in**

In `backend/app/agents/pipeline.py`: add `from .pages import render_png` (or extend the existing pages import) and `from .. import boxes` to imports, then add this helper at module level:

```python
def compute_field_boxes(fields: list[dict], pages: list[dict]) -> dict:
    """Field -> normalized box, mapped from OCR words on the first page. Empty when
    OCR is disabled (ocr_image returns no words) or there are no pages."""
    if not pages:
        return {}
    words = services_ocr_words(pages[0]["pdf_bytes"])
    return boxes.map_field_boxes(fields, words)


def services_ocr_words(pdf_bytes: bytes) -> list[dict]:
    from .. import ocr
    return ocr.ocr_image(render_png(pdf_bytes)).get("words", [])
```

Then in `run_pipeline`, where fields are committed, set `box` per field. Replace the existing fields loop:

```python
        db.query(ExtractedField).filter_by(document_id=document.id).delete()
        for f in ctx.fields:
            db.add(ExtractedField(document_id=document.id, original_value=f["field_value"],
                                  org_id=document.org_id, **f))
```

with:

```python
        field_boxes = compute_field_boxes(ctx.fields, ctx.pages)
        db.query(ExtractedField).filter_by(document_id=document.id).delete()
        for f in ctx.fields:
            db.add(ExtractedField(document_id=document.id, original_value=f["field_value"],
                                  org_id=document.org_id, box=field_boxes.get(f["field_name"]), **f))
```

- [ ] **Step 4: Add `box` to `serialize`**

In `backend/app/main.py` `serialize`, in the per-field dict add `"box":f.box` (alongside `source_quote` and `grounded`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python3 -m pytest tests/test_w2_pipeline_boxes.py tests/test_phase2_pipeline.py tests/test_review_endpoint.py -v`
Expected: PASS (new tests + existing pipeline/endpoint tests; `ocr_image` returns no words by default so `box` is null in the suite).

- [ ] **Step 6: Commit**

```bash
git add backend/app/agents/pipeline.py backend/app/main.py backend/tests/test_w2_pipeline_boxes.py
git commit -m "feat(w2): compute + persist + serialize field bounding boxes"
```

### Task C4: `GET /document/{id}/image` — serve the page image

**Files:**
- Modify: `backend/app/main.py` (add endpoint after `document` GET, ~line 352)
- Create: `backend/tests/test_w2_image_endpoint.py`

**Interfaces:**
- Consumes: `storage.get_storage().open`, `pages.render_png`.
- Produces: `GET /document/{id}/image` → PNG bytes (`image/png`), org-scoped + auth-required; 404 if the document isn't found in the caller's org.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_w2_image_endpoint.py
import fitz
from app.models import Document
from app import storage

def _pdf_bytes():
    d = fitz.open(); d.new_page(); b = d.tobytes(); d.close(); return b

def test_image_endpoint_returns_png(client, db_session, monkeypatch):
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="key1", org_id=1)
    db_session.add(doc); db_session.commit()
    monkeypatch.setattr(storage, "get_storage", lambda: type("S", (), {"open": staticmethod(lambda k: _pdf_bytes())})())
    r = client.get(f"/document/{doc.id}/image")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

def test_image_endpoint_404_for_missing(client):
    assert client.get("/document/999999/image").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_w2_image_endpoint.py -v`
Expected: FAIL (404 route or wrong status — endpoint not defined).

- [ ] **Step 3: Implement the endpoint**

In `backend/app/main.py`, add `from .agents.pages import render_png` to imports and, after the `document` GET endpoint, add:

```python
@app.get("/document/{document_id}/image")
def document_image(document_id: int, db: Session = Depends(get_db),
                   _: User = Depends(auth.get_current_user)):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    raw = get_storage().open(doc.stored_path)
    return Response(content=render_png(raw), media_type="image/png")
```

Add `Response` to the FastAPI response imports: `from fastapi.responses import JSONResponse, Response, StreamingResponse`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python3 -m pytest tests/test_w2_image_endpoint.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_w2_image_endpoint.py
git commit -m "feat(w2): serve rendered page image for the review viewer"
```

### Task C5: Frontend — image fetch helper + `DocumentViewer` with click-to-highlight

**Files:**
- Modify: `frontend/lib/api.ts` (add `fetchImageUrl`)
- Create: `frontend/app/components/DocumentViewer.tsx`
- Create: `frontend/app/document-viewer.test.tsx`

**Interfaces:**
- Consumes: `getToken`, `API` base (from `lib/api.ts`).
- Produces:
  - `fetchImageUrl(path: string) -> Promise<string>` — fetches an auth'd image and returns an object URL.
  - `<DocumentViewer imageUrl fields activeField onPickField />` — renders the image with an SVG overlay of each field's `box`; clicking a box calls `onPickField(field_name)`; the box matching `activeField` is emphasized.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/app/document-viewer.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import DocumentViewer from "./components/DocumentViewer";

const fields = [
  { field_name: "vendor_name", box: [0.1, 0.2, 0.3, 0.25] },
  { field_name: "total", box: [0.6, 0.8, 0.7, 0.85] },
  { field_name: "notes", box: null },
];

describe("DocumentViewer", () => {
  it("renders a box only for fields that have one", () => {
    render(<DocumentViewer imageUrl="blob:x" fields={fields} activeField={null} onPickField={() => {}} />);
    expect(screen.getByTestId("box-vendor_name")).toBeTruthy();
    expect(screen.getByTestId("box-total")).toBeTruthy();
    expect(screen.queryByTestId("box-notes")).toBeNull();
  });

  it("calls onPickField when a box is clicked", () => {
    const onPick = vi.fn();
    render(<DocumentViewer imageUrl="blob:x" fields={fields} activeField={null} onPickField={onPick} />);
    fireEvent.click(screen.getByTestId("box-total"));
    expect(onPick).toHaveBeenCalledWith("total");
  });

  it("marks the active field's box", () => {
    render(<DocumentViewer imageUrl="blob:x" fields={fields} activeField="vendor_name" onPickField={() => {}} />);
    expect(screen.getByTestId("box-vendor_name").getAttribute("data-active")).toBe("true");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- document-viewer`
Expected: FAIL (cannot find `./components/DocumentViewer`).

- [ ] **Step 3: Add `fetchImageUrl` to `lib/api.ts`**

Append to `frontend/lib/api.ts`:

```ts
export async function fetchImageUrl(path: string): Promise<string> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const r = await fetch(`${API}${path}`, { headers });
  if (!r.ok) { const e: any = new Error("Image load failed"); e.status = r.status; throw e; }
  return URL.createObjectURL(await r.blob());
}
```

- [ ] **Step 4: Implement `DocumentViewer.tsx`**

```tsx
// frontend/app/components/DocumentViewer.tsx
"use client";
type F = { field_name: string; box?: number[] | null };

export default function DocumentViewer({
  imageUrl, fields, activeField, onPickField,
}: { imageUrl: string; fields: F[]; activeField: string | null; onPickField: (name: string) => void }) {
  return (
    <div className="relative w-full overflow-auto rounded-lg border border-slate-200 bg-slate-50">
      {imageUrl ? <img src={imageUrl} alt="Document page" className="block w-full" /> : null}
      <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox="0 0 1 1" preserveAspectRatio="none">
        {fields.filter(f => Array.isArray(f.box) && f.box!.length === 4).map(f => {
          const [x0, y0, x1, y1] = f.box as number[];
          const active = f.field_name === activeField;
          return (
            <rect
              key={f.field_name}
              data-testid={`box-${f.field_name}`}
              data-active={active ? "true" : "false"}
              x={x0} y={y0} width={Math.max(0, x1 - x0)} height={Math.max(0, y1 - y0)}
              onClick={() => onPickField(f.field_name)}
              className="pointer-events-auto cursor-pointer"
              fill={active ? "rgba(16,185,129,0.25)" : "rgba(59,130,246,0.12)"}
              stroke={active ? "#10b981" : "#3b82f6"}
              strokeWidth={0.004}
            />
          );
        })}
      </svg>
    </div>
  );
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npm test -- document-viewer`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add frontend/lib/api.ts frontend/app/components/DocumentViewer.tsx frontend/app/document-viewer.test.tsx
git commit -m "feat(w2): DocumentViewer with click-to-highlight boxes + auth'd image fetch"
```

### Task C6: Frontend — wire viewer into Review (split-screen) + keyboard + anomaly fix

**Files:**
- Modify: `frontend/app/page.tsx` (`Review` component: load image, split-screen with `DocumentViewer`, keyboard nav, active-field state; fix anomaly rendering; update `Doc.anomalies` type)
- Create: `frontend/app/review-highlight.test.tsx`

**Interfaces:**
- Consumes: `DocumentViewer` (C5), `fetchImageUrl` (C5), `/document/{id}/image`.
- Produces: clicking a field row highlights its box and vice versa; `j`/`k` move the active field, `a` accepts (approve) when editable; anomalies render their `.message` when dict, or the string when string.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/app/review-highlight.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { renderAnomaly } from "./page";

describe("anomaly rendering", () => {
  it("renders a string anomaly as-is", () => {
    expect(renderAnomaly("Subtotal != total")).toBe("Subtotal != total");
  });
  it("renders a dict anomaly's message", () => {
    expect(renderAnomaly({ type: "duplicate_invoice", message: "Possible duplicate of a.png", duplicate_of: 1 }))
      .toBe("Possible duplicate of a.png");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- review-highlight`
Expected: FAIL (`renderAnomaly` not exported from `./page`).

- [ ] **Step 3: Export `renderAnomaly` and fix the anomaly type + rendering**

In `frontend/app/page.tsx`:
- Change the `Doc` type's anomalies to `anomalies?:(string|{type?:string,message?:string})[]|null`.
- Add and export a helper:

```tsx
export function renderAnomaly(a: string | { message?: string }): string {
  return typeof a === "string" ? a : (a?.message || "Anomaly");
}
```

- In the `Review` anomaly chips, replace `{document.anomalies.map((a:string,i:number)=> ... {a} ...)}` with `{document.anomalies.map((a:any,i:number)=> ... {renderAnomaly(a)} ...)}`.

- [ ] **Step 4: Add the split-screen viewer + keyboard nav to `Review`**

In the `Review` component (still in `frontend/app/page.tsx`):
- Add imports at the top of the file: `import DocumentViewer from "./components/DocumentViewer";` and `import { fetchImageUrl } from "../lib/api";` (extend the existing `../lib/api` import if present).
- Add state and effects inside `Review` (after the existing `useState` lines):

```tsx
  const [imageUrl,setImageUrl]=useState<string>("");
  const [activeField,setActiveField]=useState<string|null>(null);
  useEffect(()=>{ if(!document){setImageUrl("");return;} let url="";
    fetchImageUrl(`/document/${document.id}/image`).then(u=>{url=u;setImageUrl(u);}).catch(()=>setImageUrl(""));
    return ()=>{ if(url) URL.revokeObjectURL(url); }; },[document?.id]);
  useEffect(()=>{ if(!document?.fields?.length) return;
    const onKey=(e:KeyboardEvent)=>{ const names=document.fields.map((f:any)=>f.field_name);
      const i=Math.max(0,names.indexOf(activeField));
      if(e.key==="j"){setActiveField(names[Math.min(names.length-1,i+1)]);}
      else if(e.key==="k"){setActiveField(names[Math.max(0,i-1)]);}
      else if(e.key==="a"&&editable){submit("approve",onSaved);} };
    window.addEventListener("keydown",onKey); return ()=>window.removeEventListener("keydown",onKey);
  },[document,activeField,editable]);
```

(Add `useEffect` to the existing `react` import.)
- Wrap the review body in a two-column layout: put `<DocumentViewer imageUrl={imageUrl} fields={document.fields} activeField={activeField} onPickField={setActiveField}/>` on the left and the existing field-list `<div className="max-h-[420px] overflow-auto p-3">…</div>` on the right, inside a `<div className="grid grid-cols-1 lg:grid-cols-2 gap-3">`.
- On each field `<label>`, set `onMouseEnter={()=>setActiveField(f.field_name)}` and add a highlight class when `activeField===f.field_name` (e.g. append `${activeField===f.field_name?"ring-2 ring-emerald-400":""}`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npm test`
Expected: PASS — the new `review-highlight` test plus all existing frontend tests (26 baseline). If any existing test that renders `Review` breaks because it doesn't mock `/document/{id}/image`, ensure the `fetchImageUrl` failure path is swallowed (the `.catch(()=>setImageUrl(""))` above) so the component still renders.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/page.tsx frontend/app/review-highlight.test.tsx
git commit -m "feat(w2): split-screen review with click/keyboard field highlighting + anomaly rendering fix"
```

---

## Phase D — W4: customer-facing ROI dashboard

### Task D1: `GET /admin/roi` — the metrics the dashboard needs

**Files:**
- Modify: `backend/app/main.py` (add endpoint after `admin_metrics`, ~line 346)
- Create: `backend/tests/test_w4_roi.py`

**Interfaces:**
- Produces: `GET /admin/roi` (admin-only) → `{"total": int, "auto_approved": int, "reviewed": int, "stp_rate": float|None, "avg_review_seconds": float|None}` where `stp_rate = auto_approved/total` and `avg_review_seconds` is the mean `processing_time` over non-auto-approved processed docs (proxy for handling time). Dollar math is computed client-side from buyer inputs.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_w4_roi.py
from app.models import Document

def test_roi_reports_stp_and_counts(client, db_session):
    db_session.add_all([
        Document(filename="a", document_type="invoice", stored_path="a", org_id=1,
                 status="approved", auto_approved=True, confidence=0.99, processing_time=3.0),
        Document(filename="b", document_type="invoice", stored_path="b", org_id=1,
                 status="approved", auto_approved=False, confidence=0.8, processing_time=5.0),
        Document(filename="c", document_type="invoice", stored_path="c", org_id=1,
                 status="review_required", auto_approved=False, confidence=0.7, processing_time=7.0),
    ])
    db_session.commit()
    r = client.get("/admin/roi")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 3 and data["auto_approved"] == 1
    assert round(data["stp_rate"], 3) == round(1/3, 3)
    assert data["avg_review_seconds"] == 6.0  # mean of 5.0 and 7.0 (non-auto-approved)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_w4_roi.py -v`
Expected: FAIL (route 404).

- [ ] **Step 3: Implement the endpoint**

In `backend/app/main.py`, after `admin_metrics`, add:

```python
@app.get("/admin/roi")
def admin_roi(db: Session = Depends(get_db), _: User = Depends(auth.require_role("admin"))):
    total = db.query(Document).count()
    auto = db.query(Document).filter(Document.auto_approved.is_(True)).count()
    reviewed = total - auto
    review_times = [t for (t,) in db.query(Document.processing_time)
                    .filter(Document.auto_approved.is_(False),
                            Document.processing_time.isnot(None)).all()]
    avg_review = round(sum(review_times) / len(review_times), 2) if review_times else None
    return {"total": total, "auto_approved": auto, "reviewed": reviewed,
            "stp_rate": (auto / total) if total else None,
            "avg_review_seconds": avg_review}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_w4_roi.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_w4_roi.py
git commit -m "feat(w4): /admin/roi metrics endpoint"
```

### Task D2: Frontend — ROI dashboard page with buyer-editable inputs

**Files:**
- Create: `frontend/app/roi/page.tsx`
- Create: `frontend/app/roi.test.tsx`
- Create: `frontend/lib/roi.ts` (pure calc, unit-tested)

**Interfaces:**
- Produces:
  - `computeRoi({monthlyVolume, costPerDoc, minutesPerDoc, stpRate}) -> {savedDocs, monthlySaved, hoursSaved}` in `lib/roi.ts` — pure function: `savedDocs = monthlyVolume*stpRate`, `monthlySaved = savedDocs*costPerDoc`, `hoursSaved = savedDocs*minutesPerDoc/60`.
  - `/roi` page: fetches `/admin/roi`, shows STP% and avg review time, and three editable inputs (monthly volume, cost/doc, minutes/doc) that drive a live $-saved number via `computeRoi`.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/app/roi.test.tsx
import { describe, it, expect } from "vitest";
import { computeRoi } from "../lib/roi";

describe("computeRoi", () => {
  it("computes saved docs, dollars, and hours from buyer inputs", () => {
    const out = computeRoi({ monthlyVolume: 50000, costPerDoc: 2, minutesPerDoc: 4, stpRate: 0.6 });
    expect(out.savedDocs).toBe(30000);
    expect(out.monthlySaved).toBe(60000);
    expect(out.hoursSaved).toBe(2000);
  });
  it("handles zero stpRate", () => {
    expect(computeRoi({ monthlyVolume: 100, costPerDoc: 2, minutesPerDoc: 4, stpRate: 0 }).monthlySaved).toBe(0);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- roi`
Expected: FAIL (cannot find `../lib/roi`).

- [ ] **Step 3: Implement `lib/roi.ts`**

```ts
// frontend/lib/roi.ts
export function computeRoi(
  { monthlyVolume, costPerDoc, minutesPerDoc, stpRate }:
  { monthlyVolume: number; costPerDoc: number; minutesPerDoc: number; stpRate: number },
) {
  const savedDocs = Math.round(monthlyVolume * stpRate);
  return {
    savedDocs,
    monthlySaved: Math.round(savedDocs * costPerDoc),
    hoursSaved: Math.round((savedDocs * minutesPerDoc) / 60),
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- roi`
Expected: PASS (2 tests).

- [ ] **Step 5: Implement the ROI page**

```tsx
// frontend/app/roi/page.tsx
"use client";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { computeRoi } from "../../lib/roi";

export default function RoiPage() {
  const [roi, setRoi] = useState<{ stp_rate: number | null; avg_review_seconds: number | null } | null>(null);
  const [volume, setVolume] = useState(50000);
  const [cost, setCost] = useState(2);
  const [minutes, setMinutes] = useState(4);
  useEffect(() => { api("/admin/roi").then(setRoi).catch(() => setRoi(null)); }, []);
  const stp = roi?.stp_rate ?? 0;
  const calc = computeRoi({ monthlyVolume: volume, costPerDoc: cost, minutesPerDoc: minutes, stpRate: stp });
  return (
    <main className="mx-auto max-w-3xl p-6">
      <h1 className="text-2xl font-semibold">ROI</h1>
      <div className="mt-4 grid grid-cols-2 gap-3">
        <div className="card p-4"><p className="label">Straight-through rate</p>
          <p className="mt-1 text-2xl font-semibold">{roi?.stp_rate != null ? `${Math.round(roi.stp_rate * 100)}%` : "—"}</p></div>
        <div className="card p-4"><p className="label">Avg. handling time</p>
          <p className="mt-1 text-2xl font-semibold">{roi?.avg_review_seconds != null ? `${roi.avg_review_seconds}s` : "—"}</p></div>
      </div>
      <div className="card mt-4 p-4">
        <p className="label mb-2">Your numbers</p>
        <label className="block text-sm">Monthly volume
          <input type="number" value={volume} onChange={e => setVolume(+e.target.value)} className="mt-1 w-full rounded border border-slate-200 px-2 py-1.5" /></label>
        <label className="mt-2 block text-sm">Cost per doc ($)
          <input type="number" value={cost} onChange={e => setCost(+e.target.value)} className="mt-1 w-full rounded border border-slate-200 px-2 py-1.5" /></label>
        <label className="mt-2 block text-sm">Minutes per doc
          <input type="number" value={minutes} onChange={e => setMinutes(+e.target.value)} className="mt-1 w-full rounded border border-slate-200 px-2 py-1.5" /></label>
      </div>
      <div className="card mt-4 p-4">
        <p className="label">Estimated monthly saving (from your inputs × STP)</p>
        <p className="mt-1 text-3xl font-semibold">${calc.monthlySaved.toLocaleString()}</p>
        <p className="mt-1 text-sm text-slate-500">{calc.savedDocs.toLocaleString()} docs auto-handled · {calc.hoursSaved.toLocaleString()} hours saved</p>
      </div>
    </main>
  );
}
```

- [ ] **Step 6: Run the full frontend suite**

Run: `cd frontend && npm test`
Expected: PASS (all prior + new roi test).

- [ ] **Step 7: Commit**

```bash
git add frontend/app/roi/page.tsx frontend/app/roi.test.tsx frontend/lib/roi.ts
git commit -m "feat(w4): customer ROI dashboard with buyer-editable inputs"
```

---

## Phase E — W6: PII-redaction toggle + security posture

### Task E1: Backend — mark schema fields PII + redaction helper

Schema fields already carry a `fields` JSON list (see `SchemaDefinition.fields` and `document_schemas.py`). Support an optional `"pii": true` per field and expose a redaction helper the frontend uses.

**Files:**
- Create: `backend/app/redaction.py`
- Create: `backend/tests/test_w6_redaction.py`

**Interfaces:**
- Consumes: schema field dicts (each `{"name":..., "pii"?: bool, ...}`).
- Produces: `redaction.pii_field_names(schema_fields: list[dict]) -> set[str]` and `redaction.mask(value: str | None) -> str` (returns `"••••"` for non-empty values, `""` for empty/None).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_w6_redaction.py
from app import redaction

def test_pii_field_names():
    fields = [{"name": "claimant", "pii": True}, {"name": "total"}, {"name": "ssn", "pii": True}]
    assert redaction.pii_field_names(fields) == {"claimant", "ssn"}

def test_mask():
    assert redaction.mask("John Doe") == "••••"
    assert redaction.mask("") == ""
    assert redaction.mask(None) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_w6_redaction.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.redaction'`).

- [ ] **Step 3: Implement `app/redaction.py`**

```python
# backend/app/redaction.py
"""PII redaction helpers. A schema field is PII when it carries `"pii": true`.
Masking is display-only — values remain intact in storage/export."""

def pii_field_names(schema_fields: list[dict]) -> set[str]:
    return {f["name"] for f in schema_fields if f.get("pii")}


def mask(value: str | None) -> str:
    return "••••" if value and str(value).strip() else ""
```

- [ ] **Step 4: Expose PII names on the schema response**

In `backend/app/main.py` `serialize`, the document already carries `document_type`; the frontend will read PII names from the schema list it already fetches via `/schemas`. Confirm `available_schemas` returns each field dict including any `pii` key (it returns the stored `fields` JSON verbatim — no change needed). No code change if `pii` passes through; add a test asserting passthrough:

Append to `backend/tests/test_w6_redaction.py`:

```python
def test_schema_pii_flag_passthrough(client, db_session):
    from app.models import SchemaDefinition
    db_session.add(SchemaDefinition(key="claim_x", name="Claim X",
                   fields=[{"name": "claimant", "type": "string", "pii": True}], org_id=1))
    db_session.commit()
    schemas = client.get("/schemas").json()
    claim = next(s for s in schemas if s["key"] == "claim_x")
    assert claim["fields"][0]["pii"] is True
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python3 -m pytest tests/test_w6_redaction.py -v`
Expected: PASS (3 tests). If `available_schemas` strips unknown keys, adjust it to preserve them and re-run.

- [ ] **Step 6: Commit**

```bash
git add backend/app/redaction.py backend/tests/test_w6_redaction.py
git commit -m "feat(w6): PII field flag + display-only redaction helper"
```

### Task E2: Frontend — redaction toggle masking PII fields in Review

**Files:**
- Modify: `frontend/app/page.tsx` (`Review`: a "Hide PII" toggle; mask flagged fields' displayed values)
- Create: `frontend/lib/redact.ts`
- Create: `frontend/app/redaction.test.tsx`

**Interfaces:**
- Produces: `maskValue(value: string, isPii: boolean, hide: boolean) -> string` in `lib/redact.ts` — returns `"••••"` when `isPii && hide && value`, else the value.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/app/redaction.test.tsx
import { describe, it, expect } from "vitest";
import { maskValue } from "../lib/redact";

describe("maskValue", () => {
  it("masks a PII value when hidden", () => { expect(maskValue("John", true, true)).toBe("••••"); });
  it("shows a PII value when not hidden", () => { expect(maskValue("John", true, false)).toBe("John"); });
  it("never masks a non-PII value", () => { expect(maskValue("100", false, true)).toBe("100"); });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- redaction`
Expected: FAIL (cannot find `../lib/redact`).

- [ ] **Step 3: Implement `lib/redact.ts`**

```ts
// frontend/lib/redact.ts
export function maskValue(value: string, isPii: boolean, hide: boolean): string {
  return isPii && hide && value ? "••••" : value;
}
```

- [ ] **Step 4: Wire the toggle into `Review`**

In `frontend/app/page.tsx`:
- Import: `import { maskValue } from "../lib/redact";`
- The `Review` component receives `schemas` (the `Home` component already holds schema list state used for `schemaName`) — pass the current document's schema fields down, or fetch PII names from the already-loaded schemas. Add `const [hidePii,setHidePii]=useState(true);` in `Review`.
- Compute the PII set for this doc's type: from the schema list already in scope, `const piiSet=new Set((schema?.fields||[]).filter((x:any)=>x.pii).map((x:any)=>x.name));` (thread the schema for `document.document_type` into `Review` as a prop from `Home`).
- In `FieldBody`'s read-only branch, display `maskValue(f.field_value||"", piiSet.has(f.field_name), hidePii)` instead of the raw value (pass `piiSet`/`hidePii` into `FieldBody`).
- Add a checkbox in the Review header: `<label className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={hidePii} onChange={e=>setHidePii(e.target.checked)}/> Hide PII</label>`.

- [ ] **Step 5: Run the full frontend suite**

Run: `cd frontend && npm test`
Expected: PASS (all prior + new redaction test).

- [ ] **Step 6: Commit**

```bash
git add frontend/app/page.tsx frontend/lib/redact.ts frontend/app/redaction.test.tsx
git commit -m "feat(w6): Hide-PII toggle masking flagged fields in review"
```

### Task E3: Security-posture talk-track doc

**Files:**
- Create: `docs/security-posture.md`

- [ ] **Step 1: Write the posture doc**

Create `docs/security-posture.md` covering, in plain language for a buyer conversation: authentication (JWT + RBAC roles), multi-tenant isolation (fail-closed org scoping, verified by the two-org matrix), transport (HTTPS on Vercel/Render), storage (Postgres + object storage; OCR via AWS Textract with data-handling note), PII handling today (display-only redaction toggle; values not exposed in logs), and the **named roadmap to full compliance** for healthcare: encryption-at-rest, retention/deletion, PII redaction pipeline (B13), SSO. Be explicit about what exists today vs. what is on the roadmap — do not overclaim.

- [ ] **Step 2: Commit**

```bash
git add docs/security-posture.md
git commit -m "docs(w6): security posture talk-track for demo procurement questions"
```

---

## Self-Review

**Spec coverage (W2/W4/W5/W6 slice):**
- W2 doc-viewer + click/keyboard highlighting → Tasks A1, A2, C1–C6. ✅ (OCR-box architecture per the W0 gate.)
- W5 scanned-doc OCR robustness → Tasks A1, A2, B1. ✅
- W4 buyer-editable ROI dashboard → Tasks D1, D2. ✅
- W6 PII-redaction toggle + posture → Tasks E1–E3. ✅
- Cross-cutting: OCR fail-soft + flag with free fallback (A1); mixed string/dict anomaly rendering fixed (C6). ✅
- Deferred (unchanged): full B13 encryption/retention, SSO, PO matching — named in the spec, and W6's posture doc points to them.

**Placeholder scan:** every code step shows complete code; every command shows expected output. Frontend `Review` edits (C6, E2) are described as precise insertions into a known dense file rather than a full re-paste of the 119-line component — the exact symbols, props, and lines to change are named. No "handle edge cases" / "write tests for the above" left in.

**Type consistency:** `ocr_image -> {"text","words":[{"text","box"}]}` (A1) is consumed with those keys by `enrich_pages` (B1), `map_field_boxes` (C2), and `compute_field_boxes` (C3). `box` is `[x0,y0,x1,y1]` normalized end-to-end: produced in A1, unioned in C2, stored in C1/C3, serialized in C3, consumed by `DocumentViewer` (C5) and threaded through `Review` (C6). `render_png(pdf_bytes, dpi=150)` (A2) is called with those args in B1 and C3. `computeRoi` inputs/outputs (D2) match its test. `/admin/roi` keys (D1) match what the ROI page reads (D2). `renderAnomaly` / `maskValue` / `pii_field_names` / `mask` signatures match their tests.

**Known follow-up flagged for the team (not silently changed):** `validator.py:60` already emits a string `"Possible duplicate of #<id>"` anomaly, overlapping W3's dict-based `duplicate_invoice`. This plan makes the frontend render both shapes (C6) but does NOT remove the older validator check — reconciling the two duplicate signals is a decision for the team (recommend keeping the fingerprint-based one and dropping the validator string, in a small follow-up).
