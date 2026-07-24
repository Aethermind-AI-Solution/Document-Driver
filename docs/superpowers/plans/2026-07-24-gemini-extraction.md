# Generic Gemini Extraction (B1, free-tier) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Google Gemini extraction path so document extraction is generic across any layout, at $0, with a regex fallback for resilience.

**Architecture:** A pure `_fields_from_data` helper maps an extracted `{name: value}` dict to the result list (shared by the OpenAI and Gemini paths). `gemini_extract` calls Gemini with the file bytes + a JSON response schema and falls back to regex on any error. `ai_extract` dispatches Gemini → OpenAI → fallback by which key is set.

**Tech Stack:** FastAPI/Python, `google-genai` (new dep), pytest.

## Global Constraints

- Priority: `GEMINI_API_KEY` set → Gemini; else `OPENAI_API_KEY` → OpenAI; else regex fallback.
- Gemini errors (rate limit / transient) must fall back to regex — never hard-fail.
- No behavior change when no key is set (existing tests + local dev stay green).
- Confidence stays heuristic (0.94 present / 0.55 absent). No frontend change.
- Model default `gemini-2.0-flash`, via `GEMINI_MODEL`.
- Backend tests run: `cd backend && source .venv/bin/activate && python -m pytest`.

---

### Task 1: Shared `_fields_from_data` helper + OpenAI refactor (TDD)

**Files:**
- Modify: `backend/app/services.py`
- Test: `backend/tests/test_extraction.py`

**Interfaces (produced for Task 2):**
- `_fields_from_data(data: dict, fields: list[dict], present=0.94, absent=0.55) -> list[dict]`.

- [ ] **Step 1: Write the failing unit tests**

Create `backend/tests/test_extraction.py`:

```python
from app.services import _fields_from_data

FIELDS = [{"name": "vendor_name", "label": "Vendor Name"}, {"name": "total", "label": "Total"}]


def test_present_value_stringified_high_confidence():
    out = _fields_from_data({"vendor_name": "Acme", "total": 100}, FIELDS)
    by = {o["field_name"]: o for o in out}
    assert by["vendor_name"]["field_value"] == "Acme"
    assert by["vendor_name"]["confidence"] == 0.94
    assert by["total"]["field_value"] == "100"          # numeric stringified


def test_missing_or_none_is_absent():
    out = _fields_from_data({"vendor_name": None}, FIELDS)
    by = {o["field_name"]: o for o in out}
    assert by["vendor_name"]["field_value"] is None and by["vendor_name"]["confidence"] == 0.55
    assert by["total"]["field_value"] is None and by["total"]["confidence"] == 0.55   # missing key


def test_empty_or_whitespace_string_is_absent():
    out = _fields_from_data({"vendor_name": "   ", "total": ""}, FIELDS)
    by = {o["field_name"]: o for o in out}
    assert by["vendor_name"]["field_value"] is None
    assert by["total"]["field_value"] is None


def test_non_dict_data_all_absent():
    out = _fields_from_data(None, FIELDS)
    assert all(o["field_value"] is None and o["confidence"] == 0.55 for o in out)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_extraction.py -q`
Expected: FAIL — `ImportError: cannot import name '_fields_from_data'`.

- [ ] **Step 3: Add the helper and refactor the OpenAI path**

In `backend/app/services.py`:

(a) Change the top import line from `import base64, json, re, time` to:

```python
import base64, json, os, re, time
```

(b) Add the helper just above the current `ai_extract` function:

```python
def _fields_from_data(data, fields, present=0.94, absent=0.55):
    d = data if isinstance(data, dict) else {}
    out = []
    for f in fields:
        v = d.get(f["name"])
        if isinstance(v, str) and not v.strip():
            v = None
        out.append({"field_name": f["name"], "field_value": str(v) if v is not None else None,
                    "confidence": present if v is not None else absent})
    return out
```

(c) Rename the current `ai_extract` to `openai_extract`, remove its first two lines
(the `import os` and the `if not os.getenv("OPENAI_API_KEY"): return fallback_extract(...)`
guard — dispatch now lives in the new `ai_extract`), and replace its final `return`
line with the shared helper. The function becomes:

```python
def openai_extract(text: str, fields: list[dict], source_path: str):
    from openai import OpenAI
    client = OpenAI()
    properties = {f["name"]: {"type": ["string", "null"], "description": f.get("label", f["name"])} for f in fields}
    content = [{"type": "input_text", "text": "Extract all requested values. Use null when a value is absent."}]
    path = Path(source_path)
    encoded = base64.b64encode(path.read_bytes()).decode()
    if path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
        content.append({"type":"input_image", "image_url":f"data:image/{path.suffix[1:]};base64,{encoded}"})
    else:
        content.append({"type":"input_file", "filename":path.name, "file_data":f"data:application/pdf;base64,{encoded}"})
    if text: content.append({"type":"input_text", "text": "Extracted text for reference:\n" + text[:50000]})
    response = client.responses.create(model=OPENAI_MODEL, input=[{"role":"user","content":content}], text={"format":{"type":"json_schema","name":"document_extraction","strict":True,"schema":{"type":"object","properties":properties,"required":[f["name"] for f in fields],"additionalProperties":False}}})
    return _fields_from_data(json.loads(response.output_text), fields)
```

- [ ] **Step 4: Run to verify the helper tests pass**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_extraction.py -q`
Expected: PASS — 4 passed. (Note: `ai_extract` is temporarily undefined until Task 2 re-adds it; do not run the whole suite yet — `process_document` imports it. That is fixed in Task 2.)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services.py backend/tests/test_extraction.py
git commit -m "refactor: extract shared _fields_from_data mapping helper

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Gemini adapter + priority dispatch + config/dependency

**Files:**
- Modify: `backend/app/services.py` (add `gemini_extract`, new `ai_extract`)
- Modify: `backend/app/config.py` (add `GEMINI_MODEL`)
- Modify: `backend/requirements.txt` (add `google-genai`)
- Test: `backend/tests/test_extraction.py` (append resilience + priority tests)

**Interfaces:**
- Consumes `_fields_from_data`, `fallback_extract` (Task 1).
- Produces `gemini_extract(text, fields, source_path)` and the dispatching `ai_extract`.

- [ ] **Step 1: Append the failing tests**

Add to `backend/tests/test_extraction.py`:

```python
from app.services import ai_extract, gemini_extract


def test_gemini_falls_back_to_regex_on_error(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    fields = [{"name": "invoice_number", "label": "Invoice Number"}]
    # nonexistent path forces an internal error before any network call → regex fallback
    out = gemini_extract("Invoice No: INV-9", fields, "/does/not/exist.pdf")
    assert out[0]["field_name"] == "invoice_number"
    assert out[0]["field_value"] == "INV-9"   # regex fallback found it in the text


def test_ai_extract_uses_fallback_without_keys(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = ai_extract("Total: 500", [{"name": "total", "label": "Total"}], "/x.pdf")
    assert out[0]["field_value"] == "500"
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_extraction.py -q`
Expected: FAIL — `ImportError: cannot import name 'ai_extract'` / `gemini_extract` (not defined yet after Task 1's rename).

- [ ] **Step 3: Add `GEMINI_MODEL` to config**

In `backend/app/config.py`, add after the `OPENAI_MODEL` line:

```python
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
```

- [ ] **Step 4: Add the Gemini adapter + dispatcher in `services.py`**

Change the config import line to include `GEMINI_MODEL`:

```python
from .config import GEMINI_MODEL, OPENAI_MODEL
```

Then add these two functions right below `openai_extract`:

```python
def gemini_extract(text: str, fields: list[dict], source_path: str):
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        path = Path(source_path)
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(path.suffix.lower(), "application/pdf")
        properties = {f["name"]: types.Schema(type=types.Type.STRING, nullable=True, description=f.get("label", f["name"])) for f in fields}
        schema = types.Schema(type=types.Type.OBJECT, properties=properties)
        contents = ["Extract the requested fields from this document. Use null when a value is absent; do not guess.",
                    types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)]
        if text:
            contents.append("Extracted text for reference:\n" + text[:50000])
        response = client.models.generate_content(
            model=GEMINI_MODEL, contents=contents,
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema))
        return _fields_from_data(json.loads(response.text), fields)
    except Exception:
        return fallback_extract(text, fields)


def ai_extract(text: str, fields: list[dict], source_path: str):
    if os.getenv("GEMINI_API_KEY"):
        return gemini_extract(text, fields, source_path)
    if os.getenv("OPENAI_API_KEY"):
        return openai_extract(text, fields, source_path)
    return fallback_extract(text, fields)
```

- [ ] **Step 5: Add the dependency**

In `backend/requirements.txt`, add a line:

```
google-genai>=1.0.0
```

Then install it:

Run: `cd backend && source .venv/bin/activate && pip install -r requirements.txt`
Expected: installs `google-genai` and its deps without error.

- [ ] **Step 6: Run the FULL backend suite**

Run: `cd backend && source .venv/bin/activate && python -m pytest -q`
Expected: PASS — all previous tests (gate, review, security, etc.) **plus** the new
extraction tests. Existing tests are unaffected because no key is set in the test env.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services.py backend/app/config.py backend/requirements.txt backend/tests/test_extraction.py
git commit -m "feat: generic document extraction via Google Gemini (free tier)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the implementer

- Do not change `fallback_extract`, `passes_validation`, or `process_document`.
- Keep the dense one-line style where present; the new functions may be multi-line.
- The real Gemini API call is verified live after deploy (needs a key); the tests here cover the pure helper, the resilience fallback, and the no-key dispatch — all offline.
