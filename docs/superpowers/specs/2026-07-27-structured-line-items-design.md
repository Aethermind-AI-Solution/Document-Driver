# Structured Line-Items + Clean Amounts + Dual GSTIN — Design

**Date:** 2026-07-27
**Component:** Aethermind — backend (`document_schemas`, `services`, `models` n/a) + frontend (`page.tsx`)
**Status:** Approved (design). Build via the standard workflow (plan → subagent TDD → verify).
**Grounded in:** `docs/findings-invoice-extraction.md`.

## Problem

The `Dummy_Invoice_AI_Demo.pdf` exposed three issues:
1. **Line items are a table** (Description · Qty · Unit Price · Tax · Amount, 3 rows) but extracted into one `line_items` **string** and rendered as one unreadable text box.
2. **Amounts are corrupted** — PyMuPDF decodes the ₹ glyph as the letter **"I"**, so `₹18,500` → `I18,500` in the extracted text (and thus in values and grounding).
3. **Two GSTINs** (seller `29ABCDE1234F1Z5`, buyer `27AACCA9876K1Z2`) but the invoice schema has a single `gst_number`, so one is lost.

## Goal

- Extract `line_items` as a **structured array** and render it as a **readable itemized table** in the review panel.
- **Clean the ₹→"I" artifact** so amounts read correctly everywhere.
- Enrich the invoice schema with **`seller_gstin` + `buyer_gstin`** (+ `bill_to`), replacing the lone `gst_number`.

## Decisions (from brainstorming)

- Type-aware extraction: `type:"array"` fields return an **array of row objects**; scalar fields keep the `{value, quote}` string shape.
- **No new DB table** — the array is stored as a **JSON string** in the existing `ExtractedField.field_value` (Text).
- Grounding for arrays uses the **existing token-overlap** fallback on the flattened cell text (no grounding change).
- Include seller/buyer GSTIN in this build.

## Non-goals (YAGNI)

- No dynamic / AI-suggested schemas (the "not hardcoded" fix — Schema-Author agent, deferred).
- No new DB table, no per-cell grounding badges (ground the whole `line_items` field, as now).
- No strict GSTIN regex validation — the user flagged over-rigid formats; keep GSTIN fields free-text (the LLM extracts them; no `pattern`).
- No numeric-type coercion (amounts stay strings for now).

## Backend design

### Schema (`backend/app/document_schemas.py`, `invoice`)
- Replace `gst_number` with:
  - `{"name":"seller_gstin","label":"Seller GSTIN","type":"string"}`
  - `{"name":"buyer_gstin","label":"Buyer GSTIN","type":"string"}`
- Add `{"name":"bill_to","label":"Bill To","type":"string"}` (buyer name).
- `line_items` gains a column list:
  `{"name":"line_items","label":"Line Items","type":"array","columns":["description","quantity","unit_price","tax","amount"]}`

### Amount cleaning (`extract_text` in `services.py`)
After `get_text()`, run a small normalizer that removes the mis-decoded ₹ artifact:
strip a `₹` or a word-boundary `I` that sits **immediately before a digit** (e.g. `I18,500` → `18,500`, `₹66,000` → `66,000`), without touching `I` inside words. A pure helper `_clean_text(s)` — unit-tested against the real strings (`I18,500`, `Grand Total I78,880`, and a control like `Installation` which must stay intact).

### Type-aware extraction (`openai_extract`, `gemini_extract`)
Build each field's schema by type:
- **scalar** (default): `{value: string|null, quote: string|null}` (as today).
- **array** (`f["type"]=="array"`): `{value: array|null of row-objects, quote: string|null}`, where each row object has the field's `columns` as nullable string properties. Prompt addition: *"For list/table fields, return `value` as an array of row objects using the given columns; use null when absent."*

Both providers already run their parsed `data` through `_ground_fields`.

### Grounding + storage (`_ground_fields` in `services.py`)
Branch on field type:
- **scalar:** unchanged.
- **array:** `rows = entry["value"]` (a list of dicts, or None). Store `field_value = json.dumps(rows)` when non-empty else `None`. Compute a grounding blob = all row cell values joined with spaces, and pass it as the "value" to the existing `_ground(...)` (token-overlap grounds reformatted tables). `source_quote` = the quote as today. Result dict shape is unchanged (`field_name, field_value, source_quote, grounded, confidence`), so `process_document`/`serialize` need no change.

`process_document`, `passes_validation`, `review_required`, and `serialize` are unchanged (the array field is just a JSON string in `field_value`).

## Frontend design (`frontend/app/page.tsx`, `Review`)

For each field, attempt `JSON.parse(field_value)`; if it yields a **non-empty array of objects**, render an **itemized table** (a header row from the object keys, one row per item, `overflow-x:auto`) instead of the text `<input>`. The grounded badge + source-quote line stay at the field level. Scalar fields render exactly as today (parse fails / not an array → text input). Empty/na cells show "—".

## Testing

### Backend (pytest, pure)
- **`_clean_text`:** `"I18,500"→"18,500"`, `"Grand Total I78,880"→"Grand Total 78,880"`, `"₹66,000"→"66,000"`, and `"Installation Service"` / `"Invoice"` unchanged (no stray-letter damage).
- **`_ground_fields` array branch:** given `{line_items:{value:[{description:"Scanner",qty:"2",amount:"37,000"},…], quote:…}}` + doc text containing those cells → `field_value` is the JSON of the rows, `grounded=="grounded"`, and a non-array/scalar field still behaves as before. Empty array → `absent`.
- Existing extraction/grounding tests stay green (scalar path unchanged).

### Frontend (Vitest + RTL)
- A field whose `field_value` is a JSON array of objects renders a table with the column headers and a cell value; a scalar field still renders an `<input>`. Existing tests unaffected (their mock fields are scalar strings).

### Integration (live-verify after deploy)
Upload `Dummy_Invoice_AI_Demo.pdf`: `line_items` shows a 3-row table (Scanner/Printer/Installation) with clean amounts (`18,500`, not `I18,500`); `seller_gstin`=`29…`, `buyer_gstin`=`27…`; fields grounded.

## Rollback

Revert the commits. `field_value` stays a Text column (JSON or plain string); the frontend falls back to the text input if parsing fails, so older/scalar rows are unaffected. Schema field renames only affect newly-processed documents (ephemeral DB).
