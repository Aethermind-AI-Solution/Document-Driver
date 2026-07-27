# Grounding Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fabricated confidence with a real grounded/ungrounded/unverified signal by having the extractor return a source quote per field, verifying it against the document text, and surfacing provenance in the review UI.

**Architecture:** A pure `_ground_fields` (with `_ground`/`_norm`) replaces `_fields_from_data`; all three extraction paths (OpenAI, Gemini, regex) route through it. Each LLM path returns `{value, quote}` per field. Two new `ExtractedField` columns store the quote + status; `serialize` and the review UI expose them.

**Tech Stack:** FastAPI + SQLAlchemy + pytest; Next.js/React + Vitest.

**Spec:** `docs/superpowers/specs/2026-07-24-grounding-agent-design.md`

## Global Constraints

- Confidence is **derived from grounding**, replacing the `0.94/0.55` constants:
  grounded 0.95 · unverified 0.70 · ungrounded 0.40 · absent 0.55.
- Empty/whitespace doc text ⇒ `unverified` (neutral), never a false `ungrounded`.
- Matching is whitespace/case-normalized containment; grounded if the **quote OR the value** appears in the doc text.
- `_ground_fields` **replaces** `_fields_from_data` (all three paths call it). Existing `_fields_from_data` unit tests are replaced; the fallback/dispatch tests stay.
- New columns are nullable/additive; no migration framework (fresh DB on Render; delete local `database/document_intelligence.db` once). No behavior change to `process_document`'s structure or `review_required` rule.
- Product name Aethermind (never "Atlas"). Backend tests: `cd backend && source .venv/bin/activate && python -m pytest`.

---

### Task 1: Backend grounding (TDD)

**Files:**
- Modify: `backend/app/services.py` (add `_norm`/`_ground`/`_ground_fields`; remove `_fields_from_data`; update `openai_extract`, `gemini_extract`, `fallback_extract`)
- Modify: `backend/app/models.py` (two columns)
- Modify: `backend/app/main.py` (`serialize`)
- Test: `backend/tests/test_extraction.py` (replace the `_fields_from_data` cases)

**Interfaces (produced):**
- `_ground(value, quote, hay, verifiable) -> (status:str, confidence:float)`
- `_ground_fields(data, fields, doc_text) -> list[{field_name, field_value, source_quote, grounded, confidence}]`

- [ ] **Step 1: Replace the `_fields_from_data` tests with grounding tests**

In `backend/tests/test_extraction.py`, replace lines 1–30 (the `_fields_from_data` import and its four tests) with:

```python
from app.services import _ground, _ground_fields

FIELDS = [{"name": "vendor_name", "label": "Vendor Name"}, {"name": "total", "label": "Total"}]

def _by(data, doc):
    return {o["field_name"]: o for o in _ground_fields(data, FIELDS, doc)}

def test_grounded_via_quote():
    out = _by({"vendor_name": {"value": "Acme Corp", "quote": "Vendor: Acme Corp"},
               "total": {"value": "100", "quote": "Total 100"}}, "Vendor: Acme Corp\nTotal 100")
    assert out["vendor_name"]["grounded"] == "grounded"
    assert out["vendor_name"]["confidence"] == 0.95
    assert out["vendor_name"]["source_quote"] == "Vendor: Acme Corp"
    assert out["total"]["field_value"] == "100"

def test_grounded_via_value_when_no_quote():
    out = _by({"total": {"value": "500", "quote": None}}, "Grand Total: 500")
    assert out["total"]["grounded"] == "grounded" and out["total"]["confidence"] == 0.95

def test_ungrounded_when_not_in_text():
    out = _by({"total": {"value": "9999", "quote": "Total 9999"}}, "Total: 100")
    assert out["total"]["grounded"] == "ungrounded" and out["total"]["confidence"] == 0.40

def test_unverified_when_no_doc_text():
    out = _by({"total": {"value": "100", "quote": "Total 100"}}, "")
    assert out["total"]["grounded"] == "unverified" and out["total"]["confidence"] == 0.70

def test_absent_value():
    out = _by({"vendor_name": {"value": None, "quote": None}}, "some text")
    assert out["vendor_name"]["field_value"] is None
    assert out["vendor_name"]["grounded"] == "absent" and out["vendor_name"]["confidence"] == 0.55
    assert out["vendor_name"]["source_quote"] is None

def test_whitespace_value_is_absent():
    out = _by({"total": {"value": "   ", "quote": "x"}}, "total 100")
    assert out["total"]["field_value"] is None and out["total"]["grounded"] == "absent"

def test_scalar_and_non_dict_shapes():
    out = _by({"total": "100"}, "total 100")                 # entry is a scalar, not {value,quote}
    assert out["total"]["field_value"] == "100" and out["total"]["grounded"] == "grounded"
    out2 = {o["field_name"]: o for o in _ground_fields(None, FIELDS, "x")}  # non-dict data
    assert out2["total"]["grounded"] == "absent"
```

Leave the rest of the file (the `ai_extract`/`gemini_extract` fallback tests from line 33 onward) unchanged.

- [ ] **Step 2: Run to verify the new tests fail**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_extraction.py -q`
Expected: FAIL — `ImportError: cannot import name '_ground'` (and `_ground_fields`).

- [ ] **Step 3: Add grounding helpers and remove `_fields_from_data`**

In `backend/app/services.py`, **replace** the `_fields_from_data` function (lines 65–74) with:

```python
def _norm(s):
    return re.sub(r"\s+", " ", (s or "").lower()).strip()

def _ground(value, quote, hay, verifiable):
    if value is None:
        return ("absent", 0.55)
    if not verifiable:
        return ("unverified", 0.70)
    if (quote and _norm(quote) in hay) or (_norm(value) in hay):
        return ("grounded", 0.95)
    return ("ungrounded", 0.40)

def _ground_fields(data, fields, doc_text):
    hay = _norm(doc_text)
    verifiable = bool(hay)
    d = data if isinstance(data, dict) else {}
    out = []
    for f in fields:
        entry = d.get(f["name"])
        value = entry.get("value") if isinstance(entry, dict) else entry
        quote = entry.get("quote") if isinstance(entry, dict) else None
        value = str(value) if value is not None and str(value).strip() else None
        quote = quote if isinstance(quote, str) and quote.strip() else None
        status, conf = _ground(value, quote, hay, verifiable)
        out.append({"field_name": f["name"], "field_value": value,
                    "source_quote": quote, "grounded": status, "confidence": conf})
    return out
```

- [ ] **Step 4: Route `fallback_extract` through grounding**

In `backend/app/services.py`, replace `fallback_extract`'s final `return` line (line 53):

```python
    return [{"field_name": f["name"], "field_value": pairs.get(f["name"]), "confidence": 0.96 if f["name"] in pairs else 0.62} for f in fields]
```

with:

```python
    data = {f["name"]: {"value": pairs.get(f["name"]), "quote": None} for f in fields}
    return _ground_fields(data, fields, text)
```

- [ ] **Step 5: Update `openai_extract` to return `{value, quote}` and ground**

In `openai_extract`, change the `properties` line and the prompt, and the final return:

Replace:
```python
        properties = {f["name"]: {"type": ["string", "null"], "description": f.get("label", f["name"])} for f in fields}
        content = [{"type": "input_text", "text": "Extract all requested values. Use null when a value is absent."}]
```
with:
```python
        properties = {f["name"]: {"type":"object","properties":{"value":{"type":["string","null"],"description":f.get("label",f["name"])},"quote":{"type":["string","null"],"description":"verbatim quote from the document this value was taken from"}},"required":["value","quote"],"additionalProperties":False} for f in fields}
        content = [{"type": "input_text", "text": "Extract all requested values. For each field also return a short verbatim quote from the document that the value was taken from. Use null for value and quote when a value is absent. Do not paraphrase the quote."}]
```
and replace the return line:
```python
        return _fields_from_data(json.loads(response.output_text), fields)
```
with:
```python
        return _ground_fields(json.loads(response.output_text), fields, text)
```

- [ ] **Step 6: Update `gemini_extract` likewise**

Replace:
```python
        properties = {f["name"]: types.Schema(type=types.Type.STRING, nullable=True, description=f.get("label", f["name"])) for f in fields}
```
with:
```python
        properties = {f["name"]: types.Schema(type=types.Type.OBJECT, properties={"value": types.Schema(type=types.Type.STRING, nullable=True, description=f.get("label", f["name"])), "quote": types.Schema(type=types.Type.STRING, nullable=True, description="verbatim quote from the document this value was taken from")}) for f in fields}
```
replace the prompt string in `contents`:
```python
        contents = ["Extract the requested fields from this document. Use null when a value is absent; do not guess.",
```
with:
```python
        contents = ["Extract the requested fields. For each field return its value and a short verbatim quote from the document the value was taken from; use null when a value is absent; do not paraphrase the quote.",
```
and replace the return line:
```python
        return _fields_from_data(json.loads(response.text), fields)
```
with:
```python
        return _ground_fields(json.loads(response.text), fields, text)
```

- [ ] **Step 7: Add the two `ExtractedField` columns**

In `backend/app/models.py`, add after the `edited_by_user` line (line 28):

```python
    source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    grounded: Mapped[str | None] = mapped_column(String(20), nullable=True)
```

(`Text` and `String` are already imported in this file.)

- [ ] **Step 8: Expose the new fields in `serialize`**

In `backend/app/main.py` `serialize`, in the per-field dict, add `source_quote` and `grounded`. Change:
```python
"fields":[{"id":f.id,"field_name":f.field_name,"field_value":f.field_value,"confidence":f.confidence,"validated":f.validated,"edited_by_user":f.edited_by_user} for f in d.extracted_fields],
```
to:
```python
"fields":[{"id":f.id,"field_name":f.field_name,"field_value":f.field_value,"confidence":f.confidence,"validated":f.validated,"edited_by_user":f.edited_by_user,"source_quote":f.source_quote,"grounded":f.grounded} for f in d.extracted_fields],
```

- [ ] **Step 9: Run the full backend suite**

Run: `cd backend && source .venv/bin/activate && python -m pytest -q`
Expected: PASS — the 7 new grounding tests plus all previously-passing tests (the two remaining extraction fallback tests still pass because regex values appear in the doc text → `grounded`; endpoint tests seed `ExtractedField` without the new columns, which are nullable). If a stale local DB causes an insert error in an endpoint test, that only happens if `process_document` runs in tests (it does not) — but if you hit it, `rm database/document_intelligence.db`.

- [ ] **Step 10: Commit**

```bash
git add backend/app/services.py backend/app/models.py backend/app/main.py backend/tests/test_extraction.py
git commit -m "feat: grounding — real confidence from source-quote verification

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Frontend — grounded badge + provenance

**Files:**
- Modify: `frontend/app/page.tsx` (the `Review` component's field list)

**Interfaces:** consumes `f.grounded` and `f.source_quote` from the serialized field (Task 1).

- [ ] **Step 1: Render the grounded badge + source quote**

In `frontend/app/page.tsx`, in the `Review` component, replace the field `<label>` block:

```tsx
{document.fields.map((f:any)=><label key={f.id} className={`mb-2 block rounded-lg p-3 ${f.confidence<.8?"bg-red-50":f.confidence<.9?"bg-amber-50":"bg-slate-50"}`}><span className="flex justify-between text-xs font-semibold uppercase tracking-wide text-slate-500"><span>{f.field_name.replaceAll("_"," ")}</span><span>{Math.round(f.confidence*100)}%</span></span><input className="mt-2 w-full rounded border border-slate-200 bg-white px-2 py-1.5 text-sm" defaultValue={f.field_value||""} onChange={e=>f.field_value=e.target.value}/></label>)}
```

with:

```tsx
{document.fields.map((f:any)=><label key={f.id} className={`mb-2 block rounded-lg p-3 ${f.confidence<.8?"bg-red-50":f.confidence<.9?"bg-amber-50":"bg-slate-50"}`}><span className="flex items-center justify-between text-xs font-semibold uppercase tracking-wide text-slate-500"><span>{f.field_name.replaceAll("_"," ")}</span><span className="flex items-center gap-2">{f.grounded&&<span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold normal-case tracking-normal ${f.grounded==="grounded"?"bg-emerald-100 text-emerald-700":f.grounded==="ungrounded"?"bg-rose-100 text-rose-700":f.grounded==="unverified"?"bg-amber-100 text-amber-700":"bg-slate-200 text-slate-500"}`}>{f.grounded}</span>}<span>{Math.round(f.confidence*100)}%</span></span></span><input className="mt-2 w-full rounded border border-slate-200 bg-white px-2 py-1.5 text-sm" defaultValue={f.field_value||""} onChange={e=>f.field_value=e.target.value}/>{f.source_quote&&<p className="mt-1 truncate text-[11px] text-slate-400" title={f.source_quote}>from: “{f.source_quote}”</p>}</label>)}
```

(Fields with no `grounded`/`source_quote` — e.g. existing test mocks — render exactly as before, since both are conditional.)

- [ ] **Step 2: Typecheck + run the frontend suite**

Run: `cd frontend && npx tsc --noEmit && npm test`
Expected: `tsc` clean; all existing tests pass (the mocks' fields lack `grounded`/`source_quote`, so the new markup is skipped).

- [ ] **Step 3: Commit**

```bash
git add frontend/app/page.tsx
git commit -m "feat: show grounded badge + source quote in review panel

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 4: Manual verification (controller, after merge/deploy)**

With the backend running and an LLM key set, upload the demo invoice: real fields
show a green **grounded** badge at ~95% with a "from: …" quote; a deliberately-absent
field shows **absent** at 55%; editing a value to text not in the document flips it
to **ungrounded** (rose) and flags the document for review.

---

## Notes for the implementer

- Keep the dense one-line style of `page.tsx` and `main.py`.
- Do not change `process_document`'s structure, `passes_validation`, or `review_required` — they operate on the new real confidence unchanged.
- The real Gemini/OpenAI quote-return behavior is integration — verified live (Task 2 Step 4), same precedent as the untested LLM call paths.
