# Phase 2a — Real Multi-Agent Parallel Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single synchronous `ai_extract` call and the cosmetic agent panel with a real `asyncio`-orchestrated pipeline — classifier → per-page extractor fan-out → reconciler → deterministic validator → review — with a persisted, real agent trace shown in the UI.

**Architecture:** A new `backend/app/agents/` package. Each agent is a small async unit behind a uniform `Agent` protocol; `run_pipeline` orchestrates stages, times each, and records a trace. The extractor stage fans out per page with `asyncio.gather` under a `Semaphore`, reusing the existing sync `ai_extract` via `asyncio.to_thread`. `/process` stays sync and calls `process_document`, which runs the async pipeline via `asyncio.run` (no new infra, existing sync tests preserved).

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, PyMuPDF (fitz), OpenAI (AsyncOpenAI for the classifier; existing `ai_extract` per page), Next.js frontend.

## Global Constraints

- Python 3.13; backend venv at `backend/.venv` — `source backend/.venv/bin/activate` before `pytest`; run pytest from `backend/`.
- Tests run **offline**: no real OpenAI/Gemini/network. Agents are stubbed/monkeypatched at their module boundary.
- No `pytest-asyncio`: drive async code in tests with `asyncio.run(...)`.
- Keep all existing tests green (backend 78, frontend 19). Extractor reuses `ai_extract`, so grounding/extraction behavior is unchanged.
- Reuse Phase-0/1 primitives: `ai_extract`, `_ground_fields`, `passes_validation`, `_clean_text`, `models._utcnow`, the storage layer, Alembic (migrations chain from `0002_identity`).
- Roles/RBAC and actor-stamped audit from Phase 1 stay intact; `/process` keeps `require_role("admin","reviewer")` + `rate_limit`.
- Agents call collaborators via the **module** (e.g. `services.ai_extract`, not a bound import) so tests can monkeypatch them.
- Deterministic stages (reconciler, validator) must not call an LLM.

## File Structure

- `backend/app/agents/__init__.py` — package marker.
- `backend/app/agents/base.py` — `PipelineContext`, `StageResult`, `Agent` protocol, `timed_stage` helper.
- `backend/app/agents/classifier.py` — `classify_document()` + `ClassifierAgent`.
- `backend/app/agents/pages.py` — `split_pages()`.
- `backend/app/agents/extractor.py` — `ExtractorAgent` (per-page fan-out).
- `backend/app/agents/reconciler.py` — `reconcile()` + `ReconcilerAgent`.
- `backend/app/agents/validator.py` — `validate()` + `ValidatorAgent`.
- `backend/app/agents/pipeline.py` — `run_pipeline()`.
- `backend/app/services.py` — `process_document` becomes a sync shim over `run_pipeline`.
- `backend/app/models.py` / `main.py` — `pipeline_trace` + `anomalies` columns + serialize.
- `backend/alembic/versions/0003_pipeline_trace.py` — migration.
- `backend/app/config.py` — `CLASSIFIER_MODEL`, `PIPELINE_CONCURRENCY`, `PIPELINE_STAGE_TIMEOUT`.
- `frontend/app/page.tsx`, `frontend/lib/agent-timeline.ts` — real trace panel + anomaly chips + auto-detect upload.

---

## Task 1: Pipeline foundation (base types + config)

**Files:**
- Create: `backend/app/agents/__init__.py`, `backend/app/agents/base.py`
- Modify: `backend/app/config.py`
- Test: `backend/tests/test_phase2_base.py`

**Interfaces:**
- Produces:
  - `@dataclass PipelineContext` with fields: `db, document, hint_type: str, schema: dict|None=None, pages: list=field(default_factory=list), page_results: list=field(default_factory=list), fields: list=field(default_factory=list), anomalies: list=field(default_factory=list), trace: list=field(default_factory=list), actor=None`
  - `@dataclass StageResult` with `name: str, status: str, detail: str, duration_ms: int`
  - `class Agent(Protocol): name: str; async def run(self, ctx: PipelineContext) -> StageResult`
  - `async def timed_stage(name, coro_fn, ctx) -> StageResult` — runs `coro_fn(ctx)` under `asyncio.wait_for(config.PIPELINE_STAGE_TIMEOUT)`, measures elapsed ms with `time.perf_counter`, appends a `StageResult` to `ctx.trace`, returns it. On exception/timeout, records `status="error"` with the message and re-raises only if fatal (the runner decides — here just record and return the error result).

- [ ] **Step 1: Add config** — append to `backend/app/config.py`:

```python
CLASSIFIER_MODEL = os.getenv("CLASSIFIER_MODEL", "gpt-4o-mini")
PIPELINE_CONCURRENCY = int(os.getenv("PIPELINE_CONCURRENCY", "5"))
PIPELINE_STAGE_TIMEOUT = int(os.getenv("PIPELINE_STAGE_TIMEOUT", "60"))
```

- [ ] **Step 2: Write the failing test** — `backend/tests/test_phase2_base.py`:

```python
import asyncio
from app.agents.base import PipelineContext, StageResult, timed_stage


def test_context_defaults():
    ctx = PipelineContext(db=None, document=None, hint_type="invoice")
    assert ctx.pages == [] and ctx.fields == [] and ctx.anomalies == [] and ctx.trace == []


def test_timed_stage_records_trace():
    ctx = PipelineContext(db=None, document=None, hint_type="invoice")

    async def body(c):
        c.fields.append("x")

    result = asyncio.run(timed_stage("Demo", body, ctx))
    assert result.name == "Demo" and result.status == "ok"
    assert result.duration_ms >= 0
    assert ctx.trace == [result] and ctx.fields == ["x"]


def test_timed_stage_records_error():
    ctx = PipelineContext(db=None, document=None, hint_type="invoice")

    async def boom(c):
        raise ValueError("nope")

    result = asyncio.run(timed_stage("Boom", boom, ctx))
    assert result.status == "error" and "nope" in result.detail
    assert ctx.trace[-1].status == "error"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_phase2_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents'`

- [ ] **Step 4: Implement** — `backend/app/agents/__init__.py` (empty) and `backend/app/agents/base.py`:

```python
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, Awaitable
from .. import config


@dataclass
class StageResult:
    name: str
    status: str          # "ok" | "attention" | "error"
    detail: str
    duration_ms: int


@dataclass
class PipelineContext:
    db: Any
    document: Any
    hint_type: str
    schema: dict | None = None
    pages: list = field(default_factory=list)
    page_results: list = field(default_factory=list)
    fields: list = field(default_factory=list)
    anomalies: list = field(default_factory=list)
    trace: list = field(default_factory=list)
    actor: Any = None


class Agent(Protocol):
    name: str
    async def run(self, ctx: PipelineContext) -> StageResult: ...


async def timed_stage(name: str, body: Callable[[PipelineContext], Awaitable[None]],
                      ctx: PipelineContext) -> StageResult:
    started = time.perf_counter()
    try:
        await asyncio.wait_for(body(ctx), timeout=config.PIPELINE_STAGE_TIMEOUT)
        status, detail = "ok", ""
    except Exception as exc:
        status, detail = "error", f"{type(exc).__name__}: {exc}"
    result = StageResult(name=name, status=status, detail=detail,
                         duration_ms=int((time.perf_counter() - started) * 1000))
    ctx.trace.append(result)
    return result
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_phase2_base.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/agents/__init__.py backend/app/agents/base.py backend/app/config.py backend/tests/test_phase2_base.py
git commit -m "feat: pipeline foundation — PipelineContext, StageResult, timed_stage + config"
```

---

## Task 2: Classifier agent

**Files:**
- Create: `backend/app/agents/classifier.py`
- Test: `backend/tests/test_phase2_classifier.py`

**Interfaces:**
- Consumes: `PipelineContext`, `timed_stage`, `services.available_schemas`, `services.schema_for`, `config.CLASSIFIER_MODEL`.
- Produces:
  - `async def classify_document(text: str, schema_keys: list[str]) -> tuple[str | None, float]` — LLM classification; returns `(None, 0.0)` on any error (offline-safe).
  - `class ClassifierAgent` with `name="Classifier"` and `run(ctx)`: sets `ctx.schema` + `ctx.document.document_type`. Uses the classified key when confidence ≥ 0.5 and the key is a known schema; else falls back to `ctx.hint_type` if valid, else `"invoice"`. Logs a mismatch via `services.log`. Detail like `"invoice (0.97)"`.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_phase2_classifier.py`:

```python
import asyncio
from app import services
from app.agents.base import PipelineContext
from app.agents import classifier
from app.models import Document


def _ctx(db, hint="purchase_order"):
    doc = Document(filename="x.pdf", document_type=hint, stored_path="x.pdf")
    db.add(doc); db.commit(); db.refresh(doc)
    ctx = PipelineContext(db=db, document=doc, hint_type=hint)
    ctx.pages = [{"index": 0, "pdf_bytes": b"", "text": "Invoice No: INV-1"}]
    return ctx


def test_classifier_uses_llm_result(db_session, monkeypatch):
    monkeypatch.setattr(classifier, "classify_document",
                        lambda text, keys: ("invoice", 0.97))
    ctx = _ctx(db_session)
    asyncio.run(classifier.ClassifierAgent().run(ctx))
    assert ctx.document.document_type == "invoice"
    assert ctx.schema is not None and ctx.schema["fields"]


def test_classifier_falls_back_to_hint_on_low_conf(db_session, monkeypatch):
    monkeypatch.setattr(classifier, "classify_document",
                        lambda text, keys: ("invoice", 0.10))
    ctx = _ctx(db_session, hint="purchase_order")
    asyncio.run(classifier.ClassifierAgent().run(ctx))
    assert ctx.document.document_type == "purchase_order"


def test_classifier_falls_back_on_error(db_session, monkeypatch):
    def boom(text, keys):
        return (None, 0.0)
    monkeypatch.setattr(classifier, "classify_document", boom)
    ctx = _ctx(db_session, hint="invoice")
    asyncio.run(classifier.ClassifierAgent().run(ctx))
    assert ctx.document.document_type == "invoice"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase2_classifier.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents.classifier'`

- [ ] **Step 3: Implement** — `backend/app/agents/classifier.py`:

```python
import json
from .. import config, services
from .base import PipelineContext, StageResult, timed_stage


async def classify_document(text: str, schema_keys: list[str]) -> tuple[str | None, float]:
    """Ask a cheap model which schema key fits. Returns (key, confidence) or (None, 0.0)."""
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI()
        prompt = ("Classify this document into exactly one of these type keys: "
                  f"{', '.join(schema_keys)}. Respond as JSON "
                  '{"key": "<one key>", "confidence": <0..1>}.\n\nDocument:\n' + text[:4000])
        resp = await client.responses.create(
            model=config.CLASSIFIER_MODEL,
            input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            text={"format": {"type": "json_schema", "name": "classification", "strict": True,
                             "schema": {"type": "object",
                                        "properties": {"key": {"type": "string"},
                                                       "confidence": {"type": "number"}},
                                        "required": ["key", "confidence"],
                                        "additionalProperties": False}}})
        data = json.loads(resp.output_text)
        return data.get("key"), float(data.get("confidence", 0.0))
    except Exception:
        return None, 0.0


class ClassifierAgent:
    name = "Classifier"

    async def run(self, ctx: PipelineContext) -> StageResult:
        async def body(c: PipelineContext):
            schemas = services.available_schemas(c.db)
            keys = [s["key"] for s in schemas]
            text = c.pages[0]["text"] if c.pages else ""
            key, conf = await classify_document(text, keys)
            chosen = key if (key in keys and conf >= 0.5) else (
                c.hint_type if c.hint_type in keys else "invoice")
            if key and key != c.hint_type:
                services.log(c.db, c.document.id, "Classified",
                             f"hint={c.hint_type} chosen={chosen} ({conf:.2f})", actor=c.actor)
            c.document.document_type = chosen
            c.schema = services.schema_for(c.db, chosen)
            c._detail = f"{chosen} ({conf:.2f})"
        result = await timed_stage(self.name, body, ctx)
        if getattr(ctx, "_detail", None):
            result.detail = ctx._detail
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_phase2_classifier.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/classifier.py backend/tests/test_phase2_classifier.py
git commit -m "feat: classifier agent (auto doc-type with hint/confidence fallback)"
```

---

## Task 3: Page splitting + extractor fan-out

**Files:**
- Create: `backend/app/agents/pages.py`, `backend/app/agents/extractor.py`
- Test: `backend/tests/test_phase2_extractor.py`

**Interfaces:**
- Consumes: `services.ai_extract`, `services._clean_text`, `services._ground_fields`, `config.PIPELINE_CONCURRENCY`, `PipelineContext`.
- Produces:
  - `def split_pages(data: bytes, suffix: str) -> list[dict]` — returns `[{index, pdf_bytes, text}]`. For a PDF, one entry per page (single-page PDF bytes via `fitz` `select`, text via `page.get_text()` cleaned with `services._clean_text`). For images or an unparseable PDF, one entry `{index:0, pdf_bytes:data, text:""}`.
  - `class ExtractorAgent` with `name="Extractor"` and `run(ctx)`: fans out `services.ai_extract` per page under a `Semaphore(config.PIPELINE_CONCURRENCY)` via `asyncio.to_thread`; a page that raises yields an all-absent field list (`services._ground_fields({}, fields, "")`). Populates `ctx.page_results`.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_phase2_extractor.py`:

```python
import asyncio
from app import services
from app.agents.base import PipelineContext
from app.agents import extractor
from app.models import Document

FIELDS = [{"name": "total", "label": "Total"}]


def _ctx(db):
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x.pdf")
    db.add(doc); db.commit(); db.refresh(doc)
    ctx = PipelineContext(db=db, document=doc, hint_type="invoice")
    ctx.schema = {"name": "Invoice", "fields": FIELDS}
    ctx.pages = [{"index": i, "pdf_bytes": b"p", "text": f"page {i}"} for i in range(3)]
    return ctx


def test_extractor_fans_out_over_pages(db_session, monkeypatch):
    calls = []
    def fake(text, fields, path):
        calls.append(text)
        return [{"field_name": "total", "field_value": "1", "source_quote": None,
                 "grounded": "grounded", "confidence": 0.95}]
    monkeypatch.setattr(services, "ai_extract", fake)
    ctx = _ctx(db_session)
    asyncio.run(extractor.ExtractorAgent().run(ctx))
    assert len(ctx.page_results) == 3
    assert all(r[0]["field_name"] == "total" for r in ctx.page_results)


def test_extractor_page_error_yields_absent(db_session, monkeypatch):
    def flaky(text, fields, path):
        if "page 1" in text:
            raise RuntimeError("boom")
        return [{"field_name": "total", "field_value": "1", "source_quote": None,
                 "grounded": "grounded", "confidence": 0.95}]
    monkeypatch.setattr(services, "ai_extract", flaky)
    ctx = _ctx(db_session)
    asyncio.run(extractor.ExtractorAgent().run(ctx))
    assert len(ctx.page_results) == 3
    # the failed page's field is absent
    page1 = ctx.page_results[1]
    assert page1[0]["field_value"] is None and page1[0]["grounded"] == "absent"


def test_split_pages_bad_pdf_is_single_page():
    pages = extractor_split := __import__("app.agents.pages", fromlist=["split_pages"]).split_pages(b"not-a-pdf", ".pdf")
    assert len(pages) == 1 and pages[0]["index"] == 0 and pages[0]["text"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase2_extractor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents.pages'`

- [ ] **Step 3: Implement `backend/app/agents/pages.py`:**

```python
import fitz
from .. import services


def split_pages(data: bytes, suffix: str) -> list[dict]:
    """One entry per PDF page ({index, pdf_bytes, text}); images/bad PDFs → single page."""
    if suffix.lower() != ".pdf":
        return [{"index": 0, "pdf_bytes": data, "text": ""}]
    try:
        pages = []
        with fitz.open(stream=data, filetype="pdf") as doc:
            for i in range(doc.page_count):
                one = fitz.open()
                one.insert_pdf(doc, from_page=i, to_page=i)
                pages.append({"index": i, "pdf_bytes": one.tobytes(),
                              "text": services._clean_text(doc[i].get_text())})
                one.close()
        return pages or [{"index": 0, "pdf_bytes": data, "text": ""}]
    except Exception:
        return [{"index": 0, "pdf_bytes": data, "text": ""}]
```

- [ ] **Step 4: Implement `backend/app/agents/extractor.py`:**

```python
import asyncio
import os
import tempfile
from .. import config, services
from .base import PipelineContext, StageResult, timed_stage


class ExtractorAgent:
    name = "Extractor"

    async def run(self, ctx: PipelineContext) -> StageResult:
        fields = ctx.schema["fields"]
        sem = asyncio.Semaphore(config.PIPELINE_CONCURRENCY)

        def extract_one(page: dict):
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            try:
                tmp.write(page["pdf_bytes"]); tmp.close()
                return services.ai_extract(page["text"], fields, tmp.name)
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
        result.detail = f"{len(ctx.pages)} page(s)"
        return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_phase2_extractor.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/agents/pages.py backend/app/agents/extractor.py backend/tests/test_phase2_extractor.py
git commit -m "feat: page-split util + per-page extractor fan-out (asyncio.gather + semaphore)"
```

---

## Task 4: Reconciler

**Files:**
- Create: `backend/app/agents/reconciler.py`
- Test: `backend/tests/test_phase2_reconciler.py`

**Interfaces:**
- Consumes: `PipelineContext`, `timed_stage`.
- Produces:
  - `def reconcile(page_results: list[list[dict]], fields_def: list[dict]) -> list[dict]` — merges per-page grounded field lists. Array fields (`type=="array"` and `columns`) concatenate their JSON rows across pages in page order; scalars pick the best entry by rank `grounded=3 > unverified=2 > ungrounded=1 > absent=0`, ties → first page. Returns the same field-dict shape `ai_extract` produces.
  - `class ReconcilerAgent` (`name="Reconciler"`) — sets `ctx.fields = reconcile(ctx.page_results, ctx.schema["fields"])`.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_phase2_reconciler.py`:

```python
import json
from app.agents.reconciler import reconcile

FIELDS = [{"name": "total", "label": "Total"},
          {"name": "line_items", "label": "Line Items", "type": "array",
           "columns": ["description", "amount"]}]


def _f(name, value, grounded, conf):
    return {"field_name": name, "field_value": value, "source_quote": None,
            "grounded": grounded, "confidence": conf}


def test_reconcile_scalar_prefers_grounded():
    page0 = [_f("total", "100", "ungrounded", 0.40), _f("line_items", None, "absent", 0.55)]
    page1 = [_f("total", "999", "grounded", 0.95), _f("line_items", None, "absent", 0.55)]
    out = {o["field_name"]: o for o in reconcile([page0, page1], FIELDS)}
    assert out["total"]["field_value"] == "999" and out["total"]["grounded"] == "grounded"


def test_reconcile_concatenates_array_rows_in_page_order():
    r0 = _f("line_items", json.dumps([{"description": "A", "amount": "1"}]), "grounded", 0.90)
    r1 = _f("line_items", json.dumps([{"description": "B", "amount": "2"}]), "grounded", 0.90)
    page0 = [_f("total", None, "absent", 0.55), r0]
    page1 = [_f("total", None, "absent", 0.55), r1]
    out = {o["field_name"]: o for o in reconcile([page0, page1], FIELDS)}
    rows = json.loads(out["line_items"]["field_value"])
    assert [x["description"] for x in rows] == ["A", "B"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase2_reconciler.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `backend/app/agents/reconciler.py`:**

```python
import json
from .base import PipelineContext, StageResult, timed_stage

_RANK = {"grounded": 3, "unverified": 2, "ungrounded": 1, "absent": 0}


def reconcile(page_results: list[list[dict]], fields_def: list[dict]) -> list[dict]:
    by_name = {f["name"]: f for f in fields_def}
    # index each page's entries by field_name
    per_page = [{e["field_name"]: e for e in page} for page in page_results]
    out = []
    for name, fdef in by_name.items():
        entries = [p[name] for p in per_page if name in p]
        if fdef.get("type") == "array" and fdef.get("columns"):
            rows = []
            for e in entries:                       # page order
                if e.get("field_value"):
                    try:
                        rows.extend(json.loads(e["field_value"]))
                    except (TypeError, ValueError):
                        pass
            best = max(entries, key=lambda e: _RANK.get(e.get("grounded"), 0), default=None)
            out.append({"field_name": name,
                        "field_value": json.dumps(rows) if rows else None,
                        "source_quote": best.get("source_quote") if best else None,
                        "grounded": best.get("grounded") if best else "absent",
                        "confidence": best.get("confidence") if best else 0.55})
        else:
            best = max(entries, key=lambda e: _RANK.get(e.get("grounded"), 0), default=None)
            out.append(best if best else {"field_name": name, "field_value": None,
                                          "source_quote": None, "grounded": "absent",
                                          "confidence": 0.55})
    return out


class ReconcilerAgent:
    name = "Reconciler"

    async def run(self, ctx: PipelineContext) -> StageResult:
        async def body(c: PipelineContext):
            c.fields = reconcile(c.page_results, c.schema["fields"])
        result = await timed_stage(self.name, body, ctx)
        result.detail = f"{len(ctx.fields)} field(s) from {len(ctx.page_results)} page(s)"
        return result
```

Note: `max(..., key=...)` picks the first max on ties (stable), giving first-page preference.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_phase2_reconciler.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/reconciler.py backend/tests/test_phase2_reconciler.py
git commit -m "feat: reconciler — merge per-page results (best-grounded scalars, concat arrays)"
```

---

## Task 5: Validator / anomaly agent

**Files:**
- Create: `backend/app/agents/validator.py`
- Test: `backend/tests/test_phase2_validator.py`

**Interfaces:**
- Consumes: `services.passes_validation`, `PipelineContext`, `models.Document`/`ExtractedField`.
- Produces:
  - `def _num(s) -> float | None` — parse a money string (strip everything but digits/`.`/`-`).
  - `def validate(db, document, fields: list[dict], fields_def: list[dict]) -> tuple[list[dict], list[str]]` — sets each field's `validated` (via `passes_validation`); computes anomalies: arithmetic (Σ `line_items[].amount` ≈ `total` within 1.0; and `subtotal + tax ≈ total` when both present) and duplicate (`invoice_number` + `seller_gstin` match another Document's fields). Returns `(fields, anomalies)`.
  - `class ValidatorAgent` (`name="Validator"`) — runs `validate`, stores `ctx.fields`/`ctx.anomalies`.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_phase2_validator.py`:

```python
import json
from app.agents.validator import validate
from app.models import Document, ExtractedField

FIELDS_DEF = [{"name": "invoice_number", "label": "Invoice #"},
              {"name": "seller_gstin", "label": "Seller GSTIN"},
              {"name": "total", "label": "Total", "type": "number"},
              {"name": "line_items", "label": "Line Items", "type": "array",
               "columns": ["description", "amount"]}]


def _f(name, value):
    return {"field_name": name, "field_value": value, "source_quote": None,
            "grounded": "grounded", "confidence": 0.95}


def test_validate_flags_arithmetic_mismatch(db_session):
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x.pdf")
    db_session.add(doc); db_session.commit(); db_session.refresh(doc)
    fields = [_f("invoice_number", "INV-1"), _f("seller_gstin", "GST1"),
              _f("total", "100"),
              _f("line_items", json.dumps([{"description": "a", "amount": "40"},
                                           {"description": "b", "amount": "40"}]))]
    _, anomalies = validate(db_session, doc, fields, FIELDS_DEF)
    assert any("total" in a.lower() for a in anomalies)   # 80 != 100


def test_validate_passes_when_sums_match(db_session):
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x.pdf")
    db_session.add(doc); db_session.commit(); db_session.refresh(doc)
    fields = [_f("total", "80"),
              _f("line_items", json.dumps([{"description": "a", "amount": "40"},
                                           {"description": "b", "amount": "40"}]))]
    _, anomalies = validate(db_session, doc, fields, FIELDS_DEF)
    assert anomalies == []


def test_validate_detects_duplicate(db_session):
    # existing doc with the same invoice_number + seller_gstin
    old = Document(filename="old.pdf", document_type="invoice", stored_path="old.pdf")
    db_session.add(old); db_session.flush()
    db_session.add(ExtractedField(document_id=old.id, field_name="invoice_number",
                                  field_value="INV-9", original_value="INV-9", confidence=0.9))
    db_session.add(ExtractedField(document_id=old.id, field_name="seller_gstin",
                                  field_value="GSTX", original_value="GSTX", confidence=0.9))
    new = Document(filename="new.pdf", document_type="invoice", stored_path="new.pdf")
    db_session.add(new); db_session.commit(); db_session.refresh(new)
    fields = [_f("invoice_number", "INV-9"), _f("seller_gstin", "GSTX")]
    _, anomalies = validate(db_session, new, fields, FIELDS_DEF)
    assert any("duplicate" in a.lower() for a in anomalies)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase2_validator.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `backend/app/agents/validator.py`:**

```python
import json
import re
from .. import services
from ..models import Document, ExtractedField
from .base import PipelineContext, StageResult, timed_stage


def _num(s) -> float | None:
    if s is None:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", str(s))
    try:
        return float(cleaned) if cleaned not in ("", "-", ".") else None
    except ValueError:
        return None


def _value(fields: list[dict], name: str):
    for f in fields:
        if f["field_name"] == name:
            return f.get("field_value")
    return None


def validate(db, document, fields: list[dict], fields_def: list[dict]) -> tuple[list[dict], list[str]]:
    by_name = {f["name"]: f for f in fields_def}
    for f in fields:
        fdef = by_name.get(f["field_name"], {})
        f["validated"] = services.passes_validation(fdef, f.get("field_value"))

    anomalies: list[str] = []

    # arithmetic: sum(line_items.amount) ~= total
    total = _num(_value(fields, "total"))
    li = _value(fields, "line_items")
    if total is not None and li:
        try:
            rows = json.loads(li)
            summed = sum(_num(r.get("amount")) or 0 for r in rows if isinstance(r, dict))
            if rows and abs(summed - total) > 1.0:
                anomalies.append(f"Line-item amounts sum to {summed:g} but total is {total:g}")
        except (TypeError, ValueError):
            pass

    # arithmetic: subtotal + tax ~= total
    subtotal, tax = _num(_value(fields, "subtotal")), _num(_value(fields, "tax"))
    if total is not None and subtotal is not None and tax is not None:
        if abs((subtotal + tax) - total) > 1.0:
            anomalies.append(f"Subtotal {subtotal:g} + tax {tax:g} != total {total:g}")

    # duplicate: same invoice_number + seller_gstin on another document
    inv, gst = _value(fields, "invoice_number"), _value(fields, "seller_gstin")
    if inv and gst:
        sub_inv = db.query(ExtractedField.document_id).filter_by(field_name="invoice_number", field_value=inv)
        sub_gst = db.query(ExtractedField.document_id).filter_by(field_name="seller_gstin", field_value=gst)
        dup = (db.query(Document.id)
               .filter(Document.id.in_(sub_inv), Document.id.in_(sub_gst), Document.id != document.id)
               .first())
        if dup:
            anomalies.append(f"Possible duplicate of #{dup[0]}")

    return fields, anomalies


class ValidatorAgent:
    name = "Validator"

    async def run(self, ctx: PipelineContext) -> StageResult:
        async def body(c: PipelineContext):
            c.fields, c.anomalies = validate(c.db, c.document, c.fields, c.schema["fields"])
        result = await timed_stage(self.name, body, ctx)
        result.status = "attention" if ctx.anomalies else "ok"
        result.detail = f"{len(ctx.anomalies)} anomaly(ies)" if ctx.anomalies else "ok"
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_phase2_validator.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/validator.py backend/tests/test_phase2_validator.py
git commit -m "feat: deterministic validator — schema rules + arithmetic + duplicate detection"
```

---

## Task 6: Persist trace + anomalies (model + migration + serialize)

**Files:**
- Modify: `backend/app/models.py`, `backend/app/main.py` (serialize)
- Create: `backend/alembic/versions/0003_pipeline_trace.py`
- Test: `backend/tests/test_phase2_migration.py`

**Interfaces:**
- Produces: `Document.pipeline_trace: list | None` (JSON), `Document.anomalies: list | None` (JSON); `serialize()` includes both.

- [ ] **Step 1: Add columns** — in `backend/app/models.py` `Document`, after `stored_path`:

```python
    pipeline_trace: Mapped[list | None] = mapped_column(JSON, nullable=True)
    anomalies: Mapped[list | None] = mapped_column(JSON, nullable=True)
```
(`JSON` is already imported in models.py.)

- [ ] **Step 2: Generate migration 3** — from `backend/`, using the `.env`-aside procedure (config's `load_dotenv(override=True)` shadows an exported `DATABASE_URL`):

```bash
mv ../.env ../.env.bak
DB="sqlite:///$(mktemp -d)/gen3.db"
DATABASE_URL="$DB" alembic upgrade head
DATABASE_URL="$DB" alembic revision --autogenerate -m "pipeline trace and anomalies"
mv ../.env.bak ../.env
```
Rename the generated file to `backend/alembic/versions/0003_pipeline_trace.py`. Verify `down_revision` is the identity migration's revision id (autogenerate sets it — do NOT hardcode a filename), `upgrade()` adds both JSON columns to `documents` (batch mode for SQLite), and `downgrade()` drops them.

- [ ] **Step 3: Write the failing test** — `backend/tests/test_phase2_migration.py`:

```python
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

BACKEND = Path(__file__).resolve().parents[1]


def test_migration_adds_trace_and_anomalies(tmp_path):
    url = f"sqlite:///{tmp_path / 't.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    cols = {c["name"] for c in inspect(create_engine(url)).get_columns("documents")}
    assert {"pipeline_trace", "anomalies"} <= cols
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_phase2_migration.py -v`
Expected: PASS

- [ ] **Step 5: Update serialize** — in `backend/app/main.py` `serialize`, add to the returned dict (after `review_required`):

```python
        "pipeline_trace": d.pipeline_trace, "anomalies": d.anomalies,
```

- [ ] **Step 6: Run full suite**

Run: `pytest -q`
Expected: green (existing + new migration test).

- [ ] **Step 7: Commit**

```bash
git add backend/app/models.py backend/app/main.py backend/alembic/versions/0003_pipeline_trace.py backend/tests/test_phase2_migration.py
git commit -m "feat: persist pipeline_trace + anomalies on Document (migration 0003 + serialize)"
```

---

## Task 7: Pipeline runner + process_document integration

**Files:**
- Create: `backend/app/agents/pipeline.py`
- Modify: `backend/app/services.py` (`process_document` → sync shim over `run_pipeline`)
- Modify: `backend/tests/test_phase0.py`, `backend/tests/test_phase1_storage.py` (adapt the two direct `process_document` tests to the pipeline)
- Test: `backend/tests/test_phase2_pipeline.py`

**Interfaces:**
- Consumes: all agents, storage, `services.schema_for`, `PipelineContext`.
- Produces:
  - `async def run_pipeline(db, document, hint_type, actor=None) -> Document` — builds `ctx`, loads bytes via `storage.get_storage().open(document.stored_path)`, `split_pages`, runs Classifier→Extractor→Reconciler→Validator; persists `ExtractedField`s (with `original_value`), `document.pipeline_trace` (list of stage dicts), `document.anomalies`, `document.confidence`, `document.review_required`, `document.status`, `document.processing_time`; logs "Processed"; returns the document.
  - `process_document(db, document, actor=None) -> Document` — sync shim: `return asyncio.run(run_pipeline(db, document, document.document_type, actor))`.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_phase2_pipeline.py`:

```python
import asyncio
from app import services
from app.agents import pipeline, classifier
from app.models import Document, ExtractedField


def test_run_pipeline_end_to_end(db_session, monkeypatch):
    # offline stubs
    monkeypatch.setattr(classifier, "classify_document", lambda text, keys: ("invoice", 0.99))
    monkeypatch.setattr(services, "ai_extract", lambda text, fields, path: [
        {"field_name": f["name"], "field_value": ("100" if f["name"] == "total" else None),
         "source_quote": None, "grounded": ("grounded" if f["name"] == "total" else "absent"),
         "confidence": (0.95 if f["name"] == "total" else 0.55)} for f in fields])
    # single fake page (avoid real fitz)
    monkeypatch.setattr("app.agents.pipeline.split_pages",
                        lambda data, suffix: [{"index": 0, "pdf_bytes": b"", "text": "Total 100"}])
    monkeypatch.setattr("app.agents.pipeline.get_storage",
                        lambda: type("S", (), {"open": lambda self, k: b"bytes"})())
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x.pdf")
    db_session.add(doc); db_session.commit(); db_session.refresh(doc)

    result = asyncio.run(pipeline.run_pipeline(db_session, doc, "invoice"))
    assert result.status in {"processed", "review_required"}
    assert result.pipeline_trace and {s["name"] for s in result.pipeline_trace} >= {
        "Classifier", "Extractor", "Reconciler", "Validator"}
    total = db_session.query(ExtractedField).filter_by(document_id=doc.id, field_name="total").first()
    assert total.field_value == "100" and total.original_value == "100"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase2_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents.pipeline'`

- [ ] **Step 3: Implement `backend/app/agents/pipeline.py`:**

```python
import time
from pathlib import Path
from dataclasses import asdict
from sqlalchemy.orm import Session
from .. import services
from ..models import Document, ExtractedField
from ..storage import get_storage
from .base import PipelineContext
from .pages import split_pages
from .classifier import ClassifierAgent
from .extractor import ExtractorAgent
from .reconciler import ReconcilerAgent
from .validator import ValidatorAgent

STAGES = [ClassifierAgent, ExtractorAgent, ReconcilerAgent, ValidatorAgent]


async def run_pipeline(db: Session, document: Document, hint_type: str, actor=None) -> Document:
    started = time.perf_counter()
    document.status = "processing"; db.commit()
    try:
        data = get_storage().open(document.stored_path)
        suffix = Path(document.stored_path).suffix
        ctx = PipelineContext(db=db, document=document, hint_type=hint_type, actor=actor)
        ctx.pages = split_pages(data, suffix)
        for AgentCls in STAGES:
            await AgentCls().run(ctx)

        db.query(ExtractedField).filter_by(document_id=document.id).delete()
        for f in ctx.fields:
            db.add(ExtractedField(document_id=document.id, original_value=f["field_value"], **f))
        document.pipeline_trace = [asdict(s) for s in ctx.trace]
        document.anomalies = ctx.anomalies or None
        document.confidence = sum(f["confidence"] for f in ctx.fields) / max(len(ctx.fields), 1)
        document.review_required = bool(ctx.anomalies) or any(
            f["confidence"] < .9 or not f["validated"] for f in ctx.fields)
        document.status = "review_required" if document.review_required else "processed"
        document.processing_time = round(time.perf_counter() - started, 2)
        services.log(db, document.id, "Processed",
                     f"Applied {document.document_type} schema", actor=actor)
        db.commit(); db.refresh(document); return document
    except Exception as exc:
        document.status = "error"
        services.log(db, document.id, "Processing failed", str(exc), actor=actor)
        db.commit(); raise
```
Note: `ctx.fields` entries have keys `field_name, field_value, source_quote, grounded, confidence, validated` — matching `ExtractedField(**f)` plus the explicit `original_value`.

- [ ] **Step 4: Replace `process_document` with a shim** — in `backend/app/services.py`, replace the whole `process_document` function body with:

```python
def process_document(db: Session, document: Document, actor=None) -> Document:
    """Sync entry point: runs the async agent pipeline to completion."""
    import asyncio
    from .agents.pipeline import run_pipeline
    return asyncio.run(run_pipeline(db, document, document.document_type, actor))
```
(Leave the rest of `services.py` — `ai_extract`, `_ground_fields`, etc. — unchanged; the pipeline reuses them.)

- [ ] **Step 5: Adapt the two existing direct-call tests** — `process_document` now runs the full pipeline, so these two tests must stub the pipeline's LLM boundaries and storage:
  - `backend/tests/test_phase0.py::test_process_captures_original_value`
  - `backend/tests/test_phase1_storage.py::test_process_reads_bytes_through_storage`

  For each, add (adapting to the test's existing structure): stub the classifier (`monkeypatch.setattr("app.agents.classifier.classify_document", lambda t,k: ("invoice", 0.99))`), keep the existing `services.ai_extract` stub, and stub page splitting to a single fake page (`monkeypatch.setattr("app.agents.pipeline.split_pages", lambda data, suffix: [{"index":0,"pdf_bytes":b"","text":"Total 500"}])`). `test_process_reads_bytes_through_storage` asserts storage was read — assert instead that `get_storage().open(key)` is exercised (e.g. spy on the storage's `open`, or assert `document.pipeline_trace` is populated, proving the pipeline ran end-to-end through storage). Keep each test's original core assertion (`original_value == "500"`).

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_phase2_pipeline.py tests/test_phase0.py tests/test_phase1_storage.py -v && pytest -q`
Expected: new pipeline test passes; adapted tests pass; full suite green.

- [ ] **Step 7: Commit**

```bash
git add backend/app/agents/pipeline.py backend/app/services.py backend/tests/test_phase2_pipeline.py backend/tests/test_phase0.py backend/tests/test_phase1_storage.py
git commit -m "feat: pipeline runner wiring all agents; process_document runs the async pipeline"
```

---

## Task 8: Frontend — real trace panel, anomaly chips, auto-detect upload

**Files:**
- Modify: `frontend/lib/agent-timeline.ts`, `frontend/app/page.tsx`
- Test: `frontend/lib/agent-timeline.test.ts`, `frontend/app/anomalies.test.tsx`

**Interfaces:**
- Consumes: `document.pipeline_trace` (`[{name,status,detail,duration_ms}]`) and `document.anomalies` (`string[]`) from the API.
- Produces: `traceToSteps(trace)` mapping backend trace → the panel's `AgentStep[]`; page renders real trace when present (else the existing `deriveAgentTimeline` fallback); anomaly chips; upload "Auto-detect" default.

- [ ] **Step 1: Write the failing test** — add to `frontend/lib/agent-timeline.test.ts`:

```typescript
import { traceToSteps } from "./agent-timeline";

describe("traceToSteps", () => {
  it("maps backend trace entries to agent steps with status", () => {
    const steps = traceToSteps([
      { name: "Classifier", status: "ok", detail: "invoice (0.97)", duration_ms: 120 },
      { name: "Validator", status: "attention", detail: "1 anomaly(ies)", duration_ms: 5 },
    ]);
    expect(steps.map((s) => s.agent)).toEqual(["Classifier", "Validator"]);
    expect(steps[0]).toMatchObject({ status: "complete", message: "invoice (0.97)" });
    expect(steps[1]).toMatchObject({ status: "attention" });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run lib/agent-timeline.test.ts`
Expected: FAIL — `traceToSteps` is not exported.

- [ ] **Step 3: Implement `traceToSteps`** — append to `frontend/lib/agent-timeline.ts`:

```typescript
export type TraceEntry = { name: string; status: string; detail: string; duration_ms: number };

export function traceToSteps(trace: TraceEntry[]): AgentStep[] {
  const map: Record<string, AgentStatus> = { ok: "complete", attention: "attention", error: "attention" };
  return trace.map((t) => ({
    agent: t.name,
    status: map[t.status] ?? "complete",
    message: t.detail || t.name,
  }));
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `frontend/`): `npx vitest run lib/agent-timeline.test.ts`
Expected: PASS

- [ ] **Step 5: Wire the panel + anomalies + upload option** — in `frontend/app/page.tsx`:
  - Where the agent panel is rendered, use `doc.pipeline_trace?.length ? traceToSteps(doc.pipeline_trace) : deriveAgentTimeline({...})`.
  - Render `doc.anomalies` (if any) as warning chips near the document status (e.g. amber pills listing each string).
  - In the upload control, add an "Auto-detect" option as the default document-type (sends the chosen type as a hint; auto-detect sends the existing default). Write `frontend/app/anomalies.test.tsx`:

```typescript
import { describe, it, expect } from "vitest";
import { traceToSteps } from "../lib/agent-timeline";

describe("anomaly + trace wiring", () => {
  it("a validator attention entry surfaces as an attention step", () => {
    const steps = traceToSteps([{ name: "Validator", status: "attention", detail: "1 anomaly(ies)", duration_ms: 3 }]);
    expect(steps[0].status).toBe("attention");
  });
});
```
(If rendering `page.tsx` in jsdom to assert the chips is practical, prefer a render test that mocks a document with `anomalies: ["Possible duplicate of #3"]` and asserts the chip text appears; otherwise the mapping-level assertion above plus the wiring is sufficient — note which you did in the report.)

- [ ] **Step 6: Run tests + build**

Run (from `frontend/`): `npx vitest run && npm run build`
Expected: all green; build succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/lib/agent-timeline.ts frontend/lib/agent-timeline.test.ts frontend/app/page.tsx frontend/app/anomalies.test.tsx
git commit -m "feat: render real pipeline trace + anomaly chips + auto-detect upload"
```

---

## Task 9: Docs + roadmap

**Files:**
- Modify: `.env.example`, `docs/deployment.md`, `docs/roadmap.md`

- [ ] **Step 1: `.env.example`** — add the optional pipeline knobs (with defaults, no secrets):

```
CLASSIFIER_MODEL=gpt-4o-mini
PIPELINE_CONCURRENCY=5
PIPELINE_STAGE_TIMEOUT=60
```

- [ ] **Step 2: `docs/deployment.md`** — add a short "Phase 2a pipeline" note: the new optional env vars, that migration `0003` runs via the existing `alembic upgrade head`, and that no new infrastructure is required (async queue is a later 2b cycle).

- [ ] **Step 3: `docs/roadmap.md`** — under Phase 2, mark the 2a pipeline items done (classifier, per-page parallel extractors + reconciliation, validator/anomaly, real agent panel), and note that async queue/workers + structured-logging backend (2b) and the multi-document splitter remain.

- [ ] **Step 4: Commit**

```bash
git add .env.example docs/deployment.md docs/roadmap.md
git commit -m "docs: Phase 2a pipeline env + deployment notes; roadmap update"
```

---

## Self-Review

**Spec coverage:**
- Architecture / `Agent` protocol / `PipelineContext` / runner → Tasks 1, 7. Concurrency `Semaphore` → Task 3.
- Classifier (auto-detect + hint fallback + mismatch log) → Task 2. Per-page fan-out reusing `ai_extract` via `to_thread` → Task 3. Reconciliation (best-grounded scalars, concat arrays) → Task 4. Validator (schema + arithmetic + duplicate) → Task 5. Review (confidence/anomaly → review_required) → Task 7.
- Persistence (`pipeline_trace`, `anomalies`, migration 3) → Task 6. Serialize → Task 6. Frontend real panel + anomaly chips + auto-detect → Task 8. Async `/process` via sync shim + `asyncio.run` → Task 7. Config knobs → Task 1. Error handling (partial page failure, classifier fallback, per-stage timeout) → Tasks 2/3/1. Docs → Task 9.

**Placeholder scan:** No TBD/TODO. Migration 0003 is autogenerated (deterministic) with an explicit verify checklist — standard, not a placeholder. Task 5/8 note optional stronger tests (LLM-free, jsdom render) with a clear default — the required assertions are fully specified.

**Type consistency:** `PipelineContext`/`StageResult` fields, `ctx.fields` entry shape (`field_name/field_value/source_quote/grounded/confidence/validated`), `reconcile()`/`validate()` signatures, `run_pipeline(db, document, hint_type, actor=None)`, and the frontend `TraceEntry`/`traceToSteps` shape are used consistently across tasks. `ExtractedField(**f)` matches the reconciled/validated field dict plus explicit `original_value`.

**Deviation from spec noted:** `/process` stays sync and runs the async pipeline via `asyncio.run` inside `process_document` (rather than an `async` endpoint) — preserves existing sync tests + actor threading; internal `asyncio.gather` still delivers the per-page parallelism. Same observable behavior; simpler integration.
