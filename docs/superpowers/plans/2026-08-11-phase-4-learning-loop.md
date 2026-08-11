# Phase 4 (Slice 1) — Learning Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Feed past human corrections (on approved documents) back into the extractor as few-shot hints so extraction improves over time — no fine-tuning, no schema change.

**Architecture:** `services.get_correction_hints` pulls recent approved-doc corrections for a type's fields; `services._hint_block` formats them; `ai_extract`/`openai_extract`/`gemini_extract` gain a `hints` arg that appends the block to the extractor prompt; the pipeline's `ExtractorAgent` fetches hints once (when `LEARNING_ENABLED`) and threads them through, noting the count in its trace.

**Tech Stack:** FastAPI/SQLAlchemy, existing OpenAI extractor + agent pipeline.

## Global Constraints

- Python 3.13; backend venv at `backend/.venv` — `source backend/.venv/bin/activate`; run pytest from `backend/`.
- **No new dependencies, no schema change** — reuses `ExtractedField.original_value` / `.edited_by_user` and `Document.status`.
- Tests run **offline** (no OpenAI/network); async via `asyncio.run` (no `pytest-asyncio`).
- Config (reference via the `config` module — `config.X` — so tests can monkeypatch): `LEARNING_ENABLED` (bool, default `True`), `LEARNING_MAX_HINTS_PER_FIELD` (int, default 3), `LEARNING_MAX_HINTS` (int, default 20).
- `get_correction_hints` returns `{}` on cold-start OR any error (never breaks extraction). Corrections = approved docs (`status=="approved"`) + `edited_by_user==True` + non-null `original_value`/`field_value` + `original_value != field_value` + `field_name` in the schema + same `document_type`; deduped; per-field + global caps.
- Hints are **trusted data**; the prompt block includes the guard: always extract the value THIS document contains, never copy a past value not present.
- Cold-start OR `LEARNING_ENABLED=False` → behavior identical to today.
- `services.py` currently imports `from .models import AuditLog, Document, SchemaDefinition, User` (no `ExtractedField`) and does not import the `config` module — **Task 1 must re-add `ExtractedField` and add `from . import config`.**
- Keep backend **122** / frontend **26** tests green.

## File Structure

- `backend/app/config.py` — 3 learning flags.
- `backend/app/services.py` — `get_correction_hints`, `_hint_block`, `hints=` on `ai_extract`/`openai_extract`/`gemini_extract`.
- `backend/app/agents/base.py` — `PipelineContext.hints`.
- `backend/app/agents/extractor.py` — fetch hints (when enabled) + thread + trace note.
- Tests: `backend/tests/test_phase4_hints.py` (retrieval + formatting + injection), `backend/tests/test_phase4_extractor.py` (pipeline wiring).
- Docs: `.env.example`, `docs/deployment.md`, `docs/roadmap.md`.

---

## Task 1: Config flags + `get_correction_hints`

**Files:**
- Modify: `backend/app/config.py`, `backend/app/services.py`
- Test: `backend/tests/test_phase4_hints.py`

**Interfaces:**
- Produces: `services.get_correction_hints(db, document_type: str, fields: list[dict]) -> dict[str, list[tuple[str, str]]]` — `{field_name: [(original_value, corrected_value), ...]}`.

- [ ] **Step 1: Add config** — append to `backend/app/config.py`:
```python
LEARNING_ENABLED = os.getenv("LEARNING_ENABLED", "true").lower() in ("1", "true", "yes")
LEARNING_MAX_HINTS_PER_FIELD = int(os.getenv("LEARNING_MAX_HINTS_PER_FIELD", "3"))
LEARNING_MAX_HINTS = int(os.getenv("LEARNING_MAX_HINTS", "20"))
```

- [ ] **Step 2: Fix services imports** — in `backend/app/services.py`, add `ExtractedField` back to the models import and add the config module import:
```python
from .models import AuditLog, Document, ExtractedField, SchemaDefinition, User
from . import config
```
(Keep the existing `from .config import GEMINI_MODEL, OPENAI_MODEL` line.)

- [ ] **Step 3: Write the failing tests** — `backend/tests/test_phase4_hints.py`:
```python
from app.services import get_correction_hints
from app.models import Document, ExtractedField

FIELDS = [{"name": "total"}, {"name": "seller_name"}]


def _approved_correction(db, dtype, field, original, corrected, status="approved", edited=True):
    doc = Document(filename="x", document_type=dtype, stored_path="x", status=status)
    db.add(doc); db.flush()
    db.add(ExtractedField(document_id=doc.id, field_name=field, original_value=original,
                          field_value=corrected, edited_by_user=edited, confidence=0.9))
    db.commit()
    return doc


def test_hints_from_approved_changed_edits(db_session):
    _approved_correction(db_session, "invoice", "total", "1,00", "100")
    assert get_correction_hints(db_session, "invoice", FIELDS) == {"total": [("1,00", "100")]}


def test_excludes_non_approved(db_session):
    _approved_correction(db_session, "invoice", "total", "5", "50", status="review_required")
    assert get_correction_hints(db_session, "invoice", FIELDS) == {}


def test_excludes_unchanged_and_unedited(db_session):
    _approved_correction(db_session, "invoice", "total", "9", "9")               # unchanged
    _approved_correction(db_session, "invoice", "seller_name", "A", "B", edited=False)  # not edited
    assert get_correction_hints(db_session, "invoice", FIELDS) == {}


def test_excludes_other_type_and_unknown_field(db_session):
    _approved_correction(db_session, "purchase_order", "total", "1", "2")        # other type
    _approved_correction(db_session, "invoice", "mystery", "1", "2")             # field not in schema
    assert get_correction_hints(db_session, "invoice", FIELDS) == {}


def test_per_field_and_global_caps(db_session, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "LEARNING_MAX_HINTS_PER_FIELD", 2)
    monkeypatch.setattr(config, "LEARNING_MAX_HINTS", 3)
    for i in range(5):
        _approved_correction(db_session, "invoice", "total", f"a{i}", f"b{i}")
    for i in range(5):
        _approved_correction(db_session, "invoice", "seller_name", f"c{i}", f"d{i}")
    hints = get_correction_hints(db_session, "invoice", FIELDS)
    assert len(hints["total"]) == 2                       # per-field cap
    assert sum(len(v) for v in hints.values()) == 3       # global cap


def test_dedups_identical_pairs(db_session):
    _approved_correction(db_session, "invoice", "total", "1,00", "100")
    _approved_correction(db_session, "invoice", "total", "1,00", "100")
    assert get_correction_hints(db_session, "invoice", FIELDS) == {"total": [("1,00", "100")]}
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/test_phase4_hints.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_correction_hints'`

- [ ] **Step 5: Implement** — add to `backend/app/services.py`:
```python
def get_correction_hints(db: Session, document_type: str, fields: list[dict]) -> dict:
    """Recent human corrections on APPROVED docs of this type →
    {field_name: [(original_value, corrected_value), ...]}. Bounded, deduped;
    {} on cold-start or any error (never breaks extraction)."""
    try:
        names = {f["name"] for f in fields}
        rows = (db.query(ExtractedField.field_name, ExtractedField.original_value, ExtractedField.field_value)
                .join(Document, ExtractedField.document_id == Document.id)
                .filter(Document.document_type == document_type,
                        Document.status == "approved",
                        ExtractedField.edited_by_user.is_(True),
                        ExtractedField.original_value.isnot(None),
                        ExtractedField.field_value.isnot(None),
                        ExtractedField.original_value != ExtractedField.field_value)
                .order_by(Document.upload_date.desc(), ExtractedField.id.desc())
                .all())
        hints: dict = {}
        seen: set = set()
        total = 0
        for name, original, corrected in rows:
            if name not in names or total >= config.LEARNING_MAX_HINTS:
                continue
            key = (name, original, corrected)
            if key in seen:
                continue
            bucket = hints.setdefault(name, [])
            if len(bucket) >= config.LEARNING_MAX_HINTS_PER_FIELD:
                continue
            bucket.append((original, corrected)); seen.add(key); total += 1
        return hints
    except Exception as exc:
        import sys
        print(f"[get_correction_hints] skipping hints — error: {exc!r}", file=sys.stderr, flush=True)
        return {}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_phase4_hints.py -v`
Expected: PASS (6 tests)

- [ ] **Step 7: Run full suite + commit**

Run: `pytest -q` → green (122 + 6).
```bash
git add backend/app/config.py backend/app/services.py backend/tests/test_phase4_hints.py
git commit -m "feat: get_correction_hints — approved-doc corrections, bounded + deduped"
```

---

## Task 2: `_hint_block` + inject hints into the extractor prompt

**Files:**
- Modify: `backend/app/services.py`
- Test: `backend/tests/test_phase4_hints.py` (append)

**Interfaces:**
- Consumes: `get_correction_hints` output shape.
- Produces: `services._hint_block(hints: dict) -> str`; `ai_extract(text, fields, source_path, hints=None)`, `openai_extract(text, fields, source_path, hints=None)`, `gemini_extract(text, fields, source_path, hints=None)` all accept + apply `hints`.

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/test_phase4_hints.py`:
```python
import json as _json
import os as _os
import tempfile


def test_hint_block_empty_is_blank():
    from app.services import _hint_block
    assert _hint_block({}) == ""


def test_hint_block_lines_and_guard():
    from app.services import _hint_block
    block = _hint_block({"total": [("1,00", "100")]})
    assert 'total: model extracted "1,00" → correct value was "100"' in block
    assert "THIS document actually contains" in block


def test_openai_extract_injects_hint_block(monkeypatch):
    import app.services as svc
    import openai
    captured = {}

    class FakeResp:
        def __init__(self, text): self.output_text = text

    class FakeResponses:
        def create(self, model, input, text):
            captured["input"] = input
            keys = list(text["format"]["schema"]["properties"].keys())
            return FakeResp(_json.dumps({k: {"value": None, "quote": None} for k in keys}))

    class FakeClient:
        responses = FakeResponses()

    monkeypatch.setattr(openai, "OpenAI", lambda: FakeClient())
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False); tmp.write(b"%PDF"); tmp.close()
    try:
        svc.openai_extract("doc text", [{"name": "total", "label": "Total"}], tmp.name,
                           hints={"total": [("1,00", "100")]})
    finally:
        _os.unlink(tmp.name)
    texts = [c["text"] for c in captured["input"][0]["content"] if c.get("type") == "input_text"]
    assert any("correct value was" in t for t in texts)


def test_ai_extract_threads_hints(monkeypatch):
    import app.services as svc
    captured = {}
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(svc, "openai_extract",
                        lambda text, fields, path, hints=None: captured.update(h=hints) or [])
    svc.ai_extract("t", [{"name": "total"}], "/x.pdf", hints={"total": [("a", "b")]})
    assert captured["h"] == {"total": [("a", "b")]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_phase4_hints.py -k "hint_block or injects or threads" -v`
Expected: FAIL — `_hint_block` missing / `hints` not accepted.

- [ ] **Step 3: Implement `_hint_block`** — add to `backend/app/services.py`:
```python
def _hint_block(hints: dict) -> str:
    if not hints:
        return ""
    lines = ["Human reviewers have previously corrected extractions for this document type. Learn the pattern:"]
    for name, pairs in hints.items():
        for original, corrected in pairs:
            lines.append(f'- {name}: model extracted "{original}" → correct value was "{corrected}"')
    lines.append("Apply the same judgment, but ALWAYS extract the value that THIS document actually contains — "
                 "never copy a past value that is not present in this document.")
    return "\n".join(lines)
```

- [ ] **Step 4: Thread `hints` through the extractors** — in `backend/app/services.py`:
  - `def ai_extract(text: str, fields: list[dict], source_path: str, hints: dict | None = None):` and pass `hints` to both branches: `return gemini_extract(text, fields, source_path, hints)` and `return openai_extract(text, fields, source_path, hints)` (fallback branch unchanged).
  - `def openai_extract(text: str, fields: list[dict], source_path: str, hints: dict | None = None):` — after the `if text: content.append(...)` line and before `response = client.responses.create(...)`, insert:
    ```python
        block = _hint_block(hints or {})
        if block: content.append({"type": "input_text", "text": block})
    ```
  - `def gemini_extract(text: str, fields: list[dict], source_path: str, hints: dict | None = None):` — after its `if text: contents.append(...)`, insert:
    ```python
        block = _hint_block(hints or {})
        if block: contents.append(block)
    ```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_phase4_hints.py -v`
Expected: PASS (10 tests total)

- [ ] **Step 6: Run full suite + commit**

Run: `pytest -q` → green. (Existing extraction tests still pass — `hints` defaults to `None`.)
```bash
git add backend/app/services.py backend/tests/test_phase4_hints.py
git commit -m "feat: inject correction hints into the extractor prompt (hint block + ai_extract hints=)"
```

---

## Task 3: Pipeline wiring — `PipelineContext.hints` + `ExtractorAgent`

**Files:**
- Modify: `backend/app/agents/base.py`, `backend/app/agents/extractor.py`
- Test: `backend/tests/test_phase4_extractor.py`

**Interfaces:**
- Consumes: `services.get_correction_hints`, `services.ai_extract(..., hints=)`, `config.LEARNING_ENABLED`.
- Produces: `PipelineContext.hints: dict`; `ExtractorAgent` fetches hints (when enabled) and passes them to `ai_extract`, with the hint count in its trace `detail`.

- [ ] **Step 1: Add the context field** — in `backend/app/agents/base.py`, add to the `PipelineContext` dataclass (after `actor`):
```python
    hints: dict = field(default_factory=dict)
```

- [ ] **Step 2: Write the failing tests** — `backend/tests/test_phase4_extractor.py`:
```python
import asyncio
from app import services, config
from app.agents.base import PipelineContext
from app.agents import extractor
from app.models import Document


def _ctx(db):
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x")
    db.add(doc); db.commit(); db.refresh(doc)
    ctx = PipelineContext(db=db, document=doc, hint_type="invoice")
    ctx.schema = {"name": "Invoice", "fields": [{"name": "total", "label": "Total"}]}
    ctx.pages = [{"index": 0, "pdf_bytes": b"p", "text": "t"}]
    return ctx


def test_extractor_fetches_and_passes_hints(db_session, monkeypatch):
    monkeypatch.setattr(config, "LEARNING_ENABLED", True)
    monkeypatch.setattr(services, "get_correction_hints", lambda db, dt, fields: {"total": [("a", "b")]})
    seen = {}

    def fake_ai(text, fields, path, hints=None):
        seen["hints"] = hints
        return [{"field_name": "total", "field_value": "1", "source_quote": None,
                 "grounded": "grounded", "confidence": 0.9}]

    monkeypatch.setattr(services, "ai_extract", fake_ai)
    ctx = _ctx(db_session)
    res = asyncio.run(extractor.ExtractorAgent().run(ctx))
    assert seen["hints"] == {"total": [("a", "b")]}
    assert "correction hints" in res.detail


def test_extractor_skips_hints_when_disabled(db_session, monkeypatch):
    monkeypatch.setattr(config, "LEARNING_ENABLED", False)
    called = {"v": False}
    monkeypatch.setattr(services, "get_correction_hints",
                        lambda *a, **k: called.__setitem__("v", True) or {})
    seen = {}
    monkeypatch.setattr(services, "ai_extract",
                        lambda text, fields, path, hints=None: seen.update(hints=hints) or
                        [{"field_name": "total", "field_value": "1", "source_quote": None,
                          "grounded": "grounded", "confidence": 0.9}])
    ctx = _ctx(db_session)
    res = asyncio.run(extractor.ExtractorAgent().run(ctx))
    assert called["v"] is False and seen["hints"] == {}
    assert "correction hints" not in res.detail
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_phase4_extractor.py -v`
Expected: FAIL — extractor doesn't fetch/pass hints yet.

- [ ] **Step 4: Implement** — in `backend/app/agents/extractor.py`, update `ExtractorAgent.run`:
```python
    async def run(self, ctx: PipelineContext) -> StageResult:
        fields = ctx.schema["fields"]
        if config.LEARNING_ENABLED:
            ctx.hints = services.get_correction_hints(ctx.db, ctx.document.document_type, fields)
        sem = asyncio.Semaphore(config.PIPELINE_CONCURRENCY)

        def extract_one(page: dict):
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            try:
                tmp.write(page["pdf_bytes"]); tmp.close()
                return services.ai_extract(page["text"], fields, tmp.name, hints=ctx.hints)
            finally:
                os.unlink(tmp.name)

        async def one(page: dict):
            async with sem:
                try:
                    return await asyncio.to_thread(extract_one, page)
                except Exception:
                    return services._ground_fields({}, fields, "")

        async def body(c: PipelineContext):
            c.page_results = list(await asyncio.gather(*[one(p) for p in c.pages]))

        result = await timed_stage(self.name, body, ctx)
        if result.status != "error":
            n = sum(len(v) for v in ctx.hints.values())
            result.detail = f"{len(ctx.pages)} page(s)" + (f", {n} correction hints" if n else "")
        return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_phase4_extractor.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Run full suite + commit**

Run: `pytest -q` → green.
```bash
git add backend/app/agents/base.py backend/app/agents/extractor.py backend/tests/test_phase4_extractor.py
git commit -m "feat: extractor fetches correction hints (when enabled) + threads to ai_extract + trace note"
```

---

## Task 4: Docs

**Files:**
- Modify: `.env.example`, `docs/deployment.md`, `docs/roadmap.md`

- [ ] **Step 1: `.env.example`** — add:
```
LEARNING_ENABLED=true
LEARNING_MAX_HINTS_PER_FIELD=3
LEARNING_MAX_HINTS=20
```

- [ ] **Step 2: `docs/deployment.md`** — add a "Phase 4 — learning loop" note: extraction now consults human corrections on **approved** documents of the same type and injects them as few-shot hints into the extractor prompt (bounded by `LEARNING_MAX_HINTS_PER_FIELD` / `LEARNING_MAX_HINTS`; toggle with `LEARNING_ENABLED`). No new deps or schema; only has an effect once documents have been corrected and approved; the Extractor trace shows the hint count.

- [ ] **Step 3: `docs/roadmap.md`** — under Phase 4, mark the **learning-loop slice shipped** (✅); note Schema-Author (dynamic schemas, F4) and deeper confidence (B4+) remain.

- [ ] **Step 4: Commit**
```bash
git add .env.example docs/deployment.md docs/roadmap.md
git commit -m "docs: Phase 4 learning loop — env, deployment, roadmap"
```

---

## Self-Review

**Spec coverage:**
- `get_correction_hints` (approved + edited + changed + same-type + in-schema; dedup; per-field + global caps; `{}` on error) → Task 1. `_hint_block` + prompt guard → Task 2. `hints=` on `ai_extract`/`openai_extract`/`gemini_extract` + injection → Task 2. `PipelineContext.hints` + `ExtractorAgent` fetch-and-pass + trace note + `LEARNING_ENABLED` gate → Task 3. Config flags → Task 1. Cold-start / disabled → default `hints={}` (Tasks 1/3 tests). Docs → Task 4.

**Placeholder scan:** No TBD/TODO. Every code step shows complete code; tests are concrete. The OpenAI-injection test stubs the client to capture the request `input` offline (no network).

**Type consistency:** `get_correction_hints(db, document_type, fields) -> dict[str, list[tuple[str,str]]]`, `_hint_block(hints) -> str`, `ai_extract(text, fields, source_path, hints=None)`, `PipelineContext.hints: dict`, and `config.LEARNING_ENABLED/LEARNING_MAX_HINTS_PER_FIELD/LEARNING_MAX_HINTS` are used consistently across tasks. Task 1 re-adds the `ExtractedField` import + `from . import config` that the extractor/hints code depends on.
