# Generic AI Extraction via Google Gemini (B1, free-tier) — Design

**Date:** 2026-07-24
**Component:** Aethermind — backend (`services.py`)
**Status:** Approved (design)

## Problem

The live demo runs the deterministic **regex fallback** (`fallback_extract`),
which only finds fields in a specific `"Label: value"` shape. Real invoices vary
wildly in layout, so fields like `vendor_name` and `gst_number` come back empty.
Regex can never be generic. Generic extraction across "any invoice" requires a
real LLM that reads the document.

## Goal

Add a **Google Gemini** extraction path (free tier, vision-capable) so extraction
works generically on any invoice/document layout — at **$0** — while keeping the
demo resilient and the existing behavior intact when no key is set.

## Decisions (from brainstorming)

- Extraction priority in `ai_extract`: **`GEMINI_API_KEY` → Gemini; else `OPENAI_API_KEY` → OpenAI; else regex fallback.**
- Model `gemini-2.0-flash` (free-tier, vision), configurable via `GEMINI_MODEL`.
- **Resilient:** any Gemini error (free-tier rate limit / transient failure) → auto-fall back to regex, so the demo never hard-fails.
- Confidence stays heuristic (present 0.94 / absent 0.55) — real confidence is a separate item (B3/W1), explicitly out of scope.

## Non-goals (YAGNI)

- No confidence rework, no grounding/citations, no auto-classification.
- No frontend change (extraction is backend-only).
- No change to the OpenAI or fallback behavior when their branch is selected.

## Design (`backend/app/services.py`)

### Shared mapping helper (pure, unit-tested)

Both AI paths produce a `{field_name: value}` dict. Extract the mapping into one
pure helper (the OpenAI path currently inlines it, untested):

```python
def _fields_from_data(data: dict, fields: list[dict], present: float = 0.94, absent: float = 0.55) -> list[dict]:
    d = data if isinstance(data, dict) else {}
    out = []
    for f in fields:
        v = d.get(f["name"])
        if isinstance(v, str) and not v.strip():   # empty/whitespace ⇒ treat as absent
            v = None
        out.append({"field_name": f["name"], "field_value": str(v) if v is not None else None,
                    "confidence": present if v is not None else absent})
    return out
```

### Gemini adapter

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
        return fallback_extract(text, fields)   # resilient: never hard-fail the demo
```

### Priority dispatch + OpenAI refactor

- Rename the current `ai_extract` body (the OpenAI path, minus the branch check)
  to `openai_extract(text, fields, source_path)` and have it end with
  `return _fields_from_data(data, fields)` instead of the inline mapping.
- New `ai_extract`:
  ```python
  def ai_extract(text, fields, source_path):
      if os.getenv("GEMINI_API_KEY"): return gemini_extract(text, fields, source_path)
      if os.getenv("OPENAI_API_KEY"): return openai_extract(text, fields, source_path)
      return fallback_extract(text, fields)
  ```
- Add `import os` to the module-level imports; add `GEMINI_MODEL` to `config.py`
  (`os.getenv("GEMINI_MODEL", "gemini-2.0-flash")`) and import it in `services.py`.

### Dependency

Add `google-genai` to `backend/requirements.txt` (imported lazily inside
`gemini_extract`, so the module still imports without it — but it must be installed
in the deployed backend).

## Data flow

`/process` → `ai_extract` → (key set) `gemini_extract` sends the file bytes +
text to Gemini with a JSON response schema → parsed dict → `_fields_from_data` →
the usual `[{field_name, field_value, confidence}]`. On any error → regex fallback.
Downstream validation/review/UI are unchanged.

## Testing

### Backend (pytest, no network)
- **Unit — `_fields_from_data`:** value present → `str(value)` + 0.94; None/missing
  key → None + 0.55; numeric value → stringified; empty/whitespace string → treated
  as absent; non-dict `data` → all absent.
- **Resilience — `gemini_extract`:** with `GEMINI_API_KEY` set but a nonexistent
  source path (forces an internal error before any network call), it returns the
  **regex-fallback** result deterministically (no network, no mocking).
- **Priority — `ai_extract`:** with neither key set, uses the regex fallback.

The real Gemini API call is integration — **live-verified after deploy** with the
user's key against the actual invoice (same untested-by-unit precedent as the
existing OpenAI path).

## Deploy

- User creates a free key at Google AI Studio (`aistudio.google.com`, no card).
- Set `GEMINI_API_KEY` (and optionally `GEMINI_MODEL`) on Render → backend
  redeploys → extraction is generic. Still $0 (free tier, ~15 req/min · ~1,500/day on Flash).

## Rollback

Unset `GEMINI_API_KEY` → instantly reverts to OpenAI-or-fallback behavior, no code
change. Revert the commits to remove the adapter/dependency.
