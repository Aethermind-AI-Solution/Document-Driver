# Grounding Agent — Design

**Date:** 2026-07-24
**Component:** Aethermind — backend (`services.py`, `models.py`, `main.py`) + frontend (`page.tsx`)
**Status:** Approved (design). Build deferred until after the current demo passes.

## Problem

Extraction confidence is a hardcoded heuristic — `_fields_from_data` sets `0.94`
if the model returned a value and `0.55` if not, identically across the regex,
OpenAI, and Gemini paths. There is no signal that an extracted value actually
came from the document. Consequences:

- The "94% on everything" the reviewer sees is fabricated (assessment.md W1).
- There is no defense against hallucinated values or values injected via document
  content (assessment.md W2 / §2.2 of the deep-dive) — a fabricated `total` is
  indistinguishable from a real one.

## Goal

Add a **grounding** step: the extractor returns a verbatim **source quote** per
field, and a pure check verifies that quote (or the value) appears in the document
text. This produces a **real** grounded / ungrounded / unverified signal that
drives confidence and human-review flagging, and exposes provenance ("this value
came from: …") to reviewers.

## Decisions (from brainstorming)

- **Derive real confidence from grounding** — replace the `0.94/0.55` constants.
- **Scanned/image docs (empty text layer) → `unverified`**, neutral (medium
  confidence), not falsely flagged as ungrounded.
- **Store the source quote per field** (provenance), not transient.

## Non-goals (YAGNI)

- No bounding boxes / layout-model grounding, no page coordinates.
- **Not** a complete injection defense: grounding catches hallucination and
  un-sourced values and raises the bar for injection (an injected value must at
  least appear in the document), but does **not** defend against an attacker who
  plants a fake value *in* the document text. That needs positional/layout
  grounding — a later item. State this honestly; don't over-claim.
- No migration framework — the two new columns rely on a fresh DB (see Deploy).

## Confidence / status model

Per field, from `(value, source_quote, doc_text)`:

| Situation | `grounded` status | `confidence` |
|-----------|-------------------|--------------|
| value present, quote **or** value found in normalized doc text | `"grounded"` | 0.95 |
| value present, not found in doc text | `"ungrounded"` | 0.40 |
| value present, doc has no text layer (`doc_text` empty) | `"unverified"` | 0.70 |
| no value | `"absent"` | 0.55 |

`ungrounded` (0.40) and `unverified` (0.70) both fall below the existing 0.9
review threshold, so they auto-flag for human review with no change to
`process_document`'s `review_required` logic.

## Backend design (`backend/app/services.py`)

### Pure grounding helpers (unit-tested — the core of this feature)

```python
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()

def _ground(value: str | None, quote: str | None, hay: str, verifiable: bool):
    """Return (status, confidence) for one field. `hay` is the normalized doc text."""
    if value is None:
        return ("absent", 0.55)
    if not verifiable:
        return ("unverified", 0.70)
    if (quote and _norm(quote) in hay) or (_norm(value) in hay):
        return ("grounded", 0.95)
    return ("ungrounded", 0.40)

def _ground_fields(data, fields, doc_text):
    """Map {name: {value, quote}} (or {name: value}) + doc text → grounded field records."""
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

`_ground_fields` **replaces `_fields_from_data`** (all three extraction paths call it).

### Extraction paths return `{value, quote}` per field

- **`openai_extract`** — change the response `json_schema` so each field property is
  an object `{value: string|null, quote: string|null}` (both required,
  `additionalProperties:false`); update the prompt: *"For each field return its
  value and a short verbatim quote from the document the value was taken from; use
  null when the value is absent; do not paraphrase the quote."* Then
  `return _ground_fields(json.loads(response.output_text), fields, text)`.
- **`gemini_extract`** — each property becomes a nested
  `types.Schema(OBJECT, properties={value: STRING nullable, quote: STRING nullable})`;
  same prompt addition; `return _ground_fields(json.loads(response.text), fields, text)`.
- **`fallback_extract`** — build `data = {name: {"value": pairs.get(name), "quote": None}}`
  and `return _ground_fields(data, fields, text)`. Regex values are grounded by
  construction (matched *from* the text), so `_norm(value) in hay` is true and they
  get `"grounded"` / 0.95 — regex finally gets a real confidence.

### `process_document`
Unchanged in structure: `for field in values` still sets `validated` and
`db.add(ExtractedField(**field))`. The `field` dict now also carries
`source_quote` and `grounded`, which map to the new columns. `document.confidence`
(mean) and `review_required` (`any(conf < .9 or not validated)`) now operate on
real numbers — no code change needed.

## Data model (`backend/app/models.py`)

Add to `ExtractedField`:
```python
source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
grounded: Mapped[str | None] = mapped_column(String(20), nullable=True)
```

## API (`backend/app/main.py`)

In `serialize`, add `"source_quote": f.source_quote, "grounded": f.grounded` to each
field dict.

## Frontend (`frontend/app/page.tsx`)

In the `Review` component's field list:
- Color/label the per-field confidence by `grounded` status: `grounded` → normal/emerald,
  `ungrounded` → rose ("not found in source"), `unverified` → amber ("no text to verify").
- Show the `source_quote` beneath the field when present (muted, e.g. *"from: '<quote>'"*),
  so reviewers can see provenance. Truncate long quotes.
- The existing confidence % stays but now reflects the real number.

The agent-panel Validation/Review messaging (`deriveAgentTimeline`) already keys off
`confidence < 0.9` / `validated`, so it continues to work with real confidence.

## Data flow

`/process` → `ai_extract` → provider returns `{name:{value,quote}}` (or regex
pairs) → `_ground_fields(data, fields, text)` verifies each against the PyMuPDF
text → grounded field records (value, quote, status, confidence) → persisted with
new columns → mean confidence + `review_required` computed from real numbers →
serialized with provenance → UI shows badge + quote.

## Error handling

- Malformed/missing model output for a field → `entry` is `None` → treated as
  `absent`. No crash.
- Both LLM paths keep their existing try/except → regex fallback (which also grounds).
- Empty/whitespace `doc_text` → every present value is `unverified` (neutral), never
  a false `ungrounded`.

## Testing

### Backend (pytest, pure — no network)
Because `_ground_fields` replaces `_fields_from_data`, the existing
`_fields_from_data` cases in `backend/tests/test_extraction.py` are replaced by
the grounding cases below (the resilience/dispatch tests in that file stay). Unit-test
`_ground` and `_ground_fields`:
- value + matching quote in text → `grounded`, 0.95.
- value in text but no/blank quote → `grounded`, 0.95 (value-fallback).
- value present, neither quote nor value in text → `ungrounded`, 0.40.
- value present, `doc_text` empty → `unverified`, 0.70.
- no value → `absent`, 0.55; `source_quote` normalized to `None`.
- `data` shaped as `{name: value}` (no dict) and non-dict `data` → handled.

### Integration (live-verify after build)
Upload the demo invoice with the LLM key set; confirm real fields come back
`grounded` at 0.95, a deliberately-absent field is `absent`, and (if a value is
edited to something not in the doc) it flips to `ungrounded` and flags for review.

## Deploy note

Adding two columns without a migration framework: `Base.metadata.create_all` does
**not** ALTER an existing table. This is fine on Render (the free-tier SQLite is
ephemeral and recreated fresh on redeploy, so `create_all` builds the table with
the new columns). **Local dev:** delete `database/document_intelligence.db` once so
it's recreated. If persistence is added later (Postgres), introduce Alembic then.

## Rollback

Revert the commits. The new columns are nullable and additive; older rows (if any
persisted) simply have `NULL` grounding, which the UI treats as "no badge."
