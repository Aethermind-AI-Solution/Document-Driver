# Invoice Extraction — Findings & Analysis

**Date:** 2026-07-27
**Source:** `Dummy_Invoice_AI_Demo.pdf` (text-layer extraction via PyMuPDF)
**Purpose:** capture the real issues behind "wrong numbers / 2 GSTINs / hardcoded fields" and the structured line-items work, so the fixes are grounded and tracked.

## Finding 1 — Currency symbol ₹ is mis-extracted as "I" (the "wrong numbers")

The PDF text layer returns every amount prefixed with a stray `I`:

```
Industrial Barcode Scanner | 2 | I18,500 | 18% | I37,000
Subtotal   I66,000
GST (18%)  I11,880
Grand Total I78,880
```

**Root cause:** PyMuPDF's `get_text()` decodes the **₹ (Indian Rupee) glyph as the ASCII letter "I"** — a font/encoding quirk, not OCR and **not a barcode** (the doc has `images=0`; "Barcode Scanner" is a *product name*). So `₹18,500` → `I18,500` in the text we feed the model and use for grounding.

**Impact:**
- The LLM may echo the corrupted `I18,500`, or read the clean value from the *image* (gpt-4o is multimodal) while our text-reference still says `I18,500` — inconsistent.
- Grounding verifies against this corrupted text, so amounts can look off.

**Fix options (cheap → thorough):**
1. **Normalize `extract_text` output** — strip a leading `I`/`₹`/`Rs.` immediately before a digit group (e.g. regex `[I₹]\s?(?=[\d])` → ``), so the reference text and grounding see `18,500`.
2. **Prompt the model** to return clean numeric values (no currency symbols/artifacts) and to trust the image over the reference text for numbers.
3. Longer term: a proper currency/number normalizer, and per-field type coercion (numbers stored as numbers, not strings).

## Finding 2 — Two GSTINs (seller + buyer); schema holds only one

- Seller GSTIN: `29ABCDE1234F1Z5` (ABC Industrial Supplies)
- Buyer GSTIN: `27AACCA9876K1Z2` (Acme Logistics, "Bill To")

The built-in `invoice` schema (`document_schemas.py`) has a single `gst_number`, so only one is captured (the model picks the seller's; the buyer's is lost). Indian tax invoices always carry **supplier + recipient GSTIN**.

**Fix:** enrich the invoice schema — `seller_gstin` + `buyer_gstin` (plus `seller_name`/`bill_to`), rather than a lone `gst_number`.

## Finding 3 — Schemas are hardcoded / format-specific

`SCHEMAS` is a fixed dict, and the regex fallback follows fixed label/format patterns. The app *does* support `POST /schemas` for custom schemas, but the **built-in schemas are static and don't adapt to a given document's actual fields** (e.g. two GSTINs, extra headers, different layouts).

**Path:**
1. **Now:** enrich the built-in invoice schema (Finding 2) so common real fields are covered.
2. **Next:** a **Schema-Author agent** (from the deep-dive) — propose fields from a few example documents → human approves → schema saved. Makes the schema layer self-extending instead of hand-maintained.
3. Consider per-field **aliases/hints** (e.g. `gstin` matches "GSTIN/GST No/Tax ID") to make extraction robust across layouts.

## Finding 4 — Line items are a table, stored/rendered as a flattened string

The table (Description · Qty · Unit Price · Tax % · Amount, 3 rows) is extracted into a single `line_items` string, then rendered in one text box — unreadable, and the ₹→I bug shows through.

**Target:** extract `line_items` as a **structured array** of `{description, qty, unit_price, tax, amount}` and render it as a proper itemized **table** in the review panel, with each cell groundable.

## Recommended sequencing

| # | Item | Size | Note |
|---|------|------|------|
| 1 | **Structured line-items** (extract array + table UI) | M | The explicit ask; Finding 4 |
| 2 | **Clean amounts** (₹→"I" normalization) | S | Finding 1; bundle with #1 so line-item amounts are clean |
| 3 | **Seller/buyer GSTIN + richer invoice schema** | S | Finding 2 |
| 4 | Dynamic / AI-suggested schemas (Schema-Author agent) | L | Finding 3; the real "not hardcoded" fix — later |
