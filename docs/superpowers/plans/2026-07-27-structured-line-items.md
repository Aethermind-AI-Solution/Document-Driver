# Structured Line-Items + Clean Amounts + Dual GSTIN — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Extract `line_items` as a structured array (rendered as a readable table), strip the ₹→"I" amount artifact, and enrich the invoice schema with seller/buyer GSTIN.

**Architecture:** Type-aware extraction — `type:"array"` fields return an array of row objects; `_ground_fields` stores that array as JSON in `field_value` and grounds it via the existing token-overlap. `extract_text` gets a `_clean_text` normalizer. The frontend renders a JSON-array field as a table.

**Spec:** `docs/superpowers/specs/2026-07-27-structured-line-items-design.md` · **Findings:** `docs/findings-invoice-extraction.md`

## Global Constraints

- Scalar-field behavior is **unchanged** (existing extraction/grounding tests stay green).
- Array field: value stored as a **JSON string** in `field_value` (no new DB table/columns); grounded via token-overlap on flattened cell text.
- `_clean_text` strips a `₹` or a boundary `I` **only immediately before a digit** — must NOT damage words (`Installation`, `Invoice`, `INV-2026`).
- No strict GSTIN regex (keep free-text). No numeric coercion. No line-item cell editing (table is read-only). No dynamic schemas.
- `process_document`, `passes_validation`, `review_required`, `serialize` unchanged. Product name Aethermind. Backend tests: `cd backend && source .venv/bin/activate && python -m pytest`.

---

### Task 1: Backend — clean amounts, structured array extraction, dual GSTIN (TDD)

**Files:** Modify `backend/app/services.py`, `backend/app/document_schemas.py`; Test `backend/tests/test_extraction.py`.

**Interfaces produced:** `_clean_text(s)->str`; `_ground_fields` now JSON-stores + grounds `type:"array"` fields; `_openai_prop(f)`/`_gemini_prop(f)` schema builders; the LLM paths request arrays for array fields.

- [ ] **Step 1: Failing tests** — append to `backend/tests/test_extraction.py`:

```python
from app.services import _clean_text


def test_clean_text_strips_rupee_artifact():
    assert _clean_text("I18,500") == "18,500"
    assert _clean_text("Grand Total I78,880") == "Grand Total 78,880"
    assert _clean_text("₹66,000") == "66,000"


def test_clean_text_leaves_words_intact():
    assert _clean_text("Installation Service") == "Installation Service"
    assert _clean_text("Invoice No. INV-2026-00125") == "Invoice No. INV-2026-00125"


def test_ground_fields_array_stores_json_and_grounds():
    import json as _json
    fields = [{"name": "line_items", "label": "Line Items", "type": "array",
               "columns": ["description", "quantity", "amount"]}]
    rows = [{"description": "Scanner", "quantity": "2", "amount": "37,000"},
            {"description": "Printer", "quantity": "1", "amount": "24,000"}]
    doc = "Scanner 2 37,000 Printer 1 24,000"
    out = _ground_fields({"line_items": {"value": rows, "quote": None}}, fields, doc)
    assert _json.loads(out[0]["field_value"]) == rows           # stored as JSON
    assert out[0]["grounded"] == "grounded"                     # token-overlap over cells


def test_ground_fields_empty_array_is_absent():
    fields = [{"name": "line_items", "label": "Line Items", "type": "array", "columns": ["description"]}]
    out = _ground_fields({"line_items": {"value": [], "quote": None}}, fields, "anything")
    assert out[0]["field_value"] is None and out[0]["grounded"] == "absent"
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_extraction.py -q`
Expected: FAIL — `cannot import name '_clean_text'`.

- [ ] **Step 3: Add `_clean_text` and use it in `extract_text`**

In `services.py`, add above `extract_text`:

```python
def _clean_text(s):
    # PyMuPDF decodes the ₹ glyph as "I"; strip a ₹ or a word-boundary "I" that sits
    # immediately before a digit (e.g. "I18,500"→"18,500") without touching words.
    return re.sub(r"(?:₹|(?<![A-Za-z0-9])I)(?=\d)", "", s or "")
```

Change `extract_text`'s PDF return line to wrap the text:

```python
        with fitz.open(p) as pdf: return _clean_text("\n".join(page.get_text() for page in pdf))
```

- [ ] **Step 4: Array-aware `_ground_fields`**

Replace the body of the `for f in fields:` loop in `_ground_fields` with:

```python
    for f in fields:
        entry = d.get(f["name"])
        raw = entry.get("value") if isinstance(entry, dict) else entry
        quote = entry.get("quote") if isinstance(entry, dict) else None
        quote = quote if isinstance(quote, str) and quote.strip() else None
        if f.get("type") == "array":
            rows = [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
            field_value = json.dumps(rows) if rows else None
            blob = " ".join(str(v) for r in rows for v in r.values() if v is not None) or None
            status, conf = _ground(blob, quote, hay, verifiable)
        else:
            field_value = str(raw) if raw is not None and str(raw).strip() else None
            status, conf = _ground(field_value, quote, hay, verifiable)
        out.append({"field_name": f["name"], "field_value": field_value,
                    "source_quote": quote, "grounded": status, "confidence": conf})
```

- [ ] **Step 5: Type-aware extraction schema builders**

Add two helpers to `services.py` (above `openai_extract`):

```python
def _openai_prop(f):
    quote = {"type": ["string", "null"], "description": "verbatim quote from the document this value was taken from"}
    if f.get("type") == "array" and f.get("columns"):
        cols = f["columns"]
        item = {"type": "object", "properties": {c: {"type": ["string", "null"]} for c in cols},
                "required": cols, "additionalProperties": False}
        value = {"type": ["array", "null"], "description": f.get("label", f["name"]), "items": item}
    else:
        value = {"type": ["string", "null"], "description": f.get("label", f["name"])}
    return {"type": "object", "properties": {"value": value, "quote": quote},
            "required": ["value", "quote"], "additionalProperties": False}


def _gemini_prop(f):
    from google.genai import types
    quote = types.Schema(type=types.Type.STRING, nullable=True, description="verbatim quote from the document this value was taken from")
    if f.get("type") == "array" and f.get("columns"):
        item = types.Schema(type=types.Type.OBJECT, properties={c: types.Schema(type=types.Type.STRING, nullable=True) for c in f["columns"]})
        value = types.Schema(type=types.Type.ARRAY, nullable=True, items=item, description=f.get("label", f["name"]))
    else:
        value = types.Schema(type=types.Type.STRING, nullable=True, description=f.get("label", f["name"]))
    return types.Schema(type=types.Type.OBJECT, properties={"value": value, "quote": quote})
```

In `openai_extract`, replace the `properties = {...}` line (currently line 104) with:
```python
        properties = {f["name"]: _openai_prop(f) for f in fields}
```
and change the first prompt string (line 105) to append the array instruction:
```python
        content = [{"type": "input_text", "text": "Extract all requested values with a short verbatim quote from the document for each. For list/table fields, return value as an array of row objects using the given columns. Use null when a value is absent; do not paraphrase the quote."}]
```

In `gemini_extract`, replace the `properties = {...}` line (currently line 127) with:
```python
        properties = {f["name"]: _gemini_prop(f) for f in fields}
```
and change the first `contents` prompt string (line 129) to:
```python
        contents = ["Extract the requested fields, each with a short verbatim quote from the document. For list/table fields, return value as an array of row objects using the columns. Use null when a value is absent; do not paraphrase the quote.",
```

- [ ] **Step 6: Enrich the invoice schema**

In `backend/app/document_schemas.py`, in the `invoice` fields:
- Replace `{"name":"gst_number","label":"GST Number","type":"string"}` with:
  `{"name":"seller_gstin","label":"Seller GSTIN","type":"string"}, {"name":"buyer_gstin","label":"Buyer GSTIN","type":"string"}, {"name":"bill_to","label":"Bill To","type":"string"}`
- Replace `{"name":"line_items","label":"Line Items","type":"array"}` with:
  `{"name":"line_items","label":"Line Items","type":"array","columns":["description","quantity","unit_price","tax","amount"]}`

- [ ] **Step 7: Run the full backend suite**

Run: `cd backend && source .venv/bin/activate && python -m pytest -q`
Expected: PASS — the 4 new tests plus all existing (scalar path unchanged; the two remaining fallback tests still ground regex values).

- [ ] **Step 8: Offline API-surface check (no key/network)**

Run:
```bash
cd backend && source .venv/bin/activate && python3 -c "
from google.genai import types
from app.services import _openai_prop, _gemini_prop
f={'name':'line_items','type':'array','columns':['description','amount'],'label':'Line Items'}
p=_openai_prop(f); assert p['properties']['value']['type']==['array','null'] and 'items' in p['properties']['value'], p
g=_gemini_prop(f); assert g.properties['value'].type==types.Type.ARRAY, g
s=_openai_prop({'name':'total','type':'number','label':'Total'})
assert s['properties']['value']['type']==['string','null']
print('OK: array + scalar prop shapes valid for both providers')
"
```
Expected: `OK: …`.

- [ ] **Step 9: Commit**

```bash
git add backend/app/services.py backend/app/document_schemas.py backend/tests/test_extraction.py
git commit -m "feat: structured line-items extraction + ₹ amount cleaning + dual GSTIN schema

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Frontend — render line-items as a table

**Files:** Modify `frontend/app/page.tsx`; Test `frontend/app/review-table.test.tsx`.

**Interfaces:** consumes `field_value` (a JSON array string for `line_items`).

- [ ] **Step 1: Failing RTL test** — create `frontend/app/review-table.test.tsx`:

```tsx
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import Home from "./page";

const doc = {
  id: 5, filename: "inv.pdf", document_type: "invoice", status: "processed",
  confidence: 0.95, upload_date: "2026-07-27T00:00:00", review_required: false,
  fields: [
    { id: 1, field_name: "total", field_value: "78,880", confidence: 0.95, validated: true, grounded: "grounded", source_quote: "Grand Total 78,880" },
    { id: 2, field_name: "line_items", confidence: 0.95, validated: true, grounded: "grounded", source_quote: null,
      field_value: JSON.stringify([{ description: "Scanner", quantity: "2", amount: "37,000" }, { description: "Printer", quantity: "1", amount: "24,000" }]) },
  ], audit: [],
};

vi.mock("../lib/api", () => ({
  getToken: () => "", setToken: () => {}, clearToken: () => {}, downloadFile: vi.fn(),
  api: vi.fn(async (path: string) => {
    if (path === "/schemas") return [{ key: "invoice", name: "Invoice", fields: [] }];
    if (path === "/documents") return [doc];
    if (path.startsWith("/document/")) return doc;
    return {};
  }),
}));

beforeEach(() => cleanup());

describe("line-items table", () => {
  it("renders an array field as a table and a scalar field as an input", async () => {
    render(<Home />);
    fireEvent.click(await screen.findByText("inv.pdf"));
    await screen.findByDisplayValue("78,880");                 // scalar → input
    expect(screen.getByText("Scanner")).toBeTruthy();          // array → table cell
    expect(screen.getByText("Printer")).toBeTruthy();
    expect(screen.getByText("description")).toBeTruthy();       // column header (raw key)
  });
});
```

- [ ] **Step 2: Run to verify fail**

Run: `cd frontend && npx vitest run app/review-table.test.tsx`
Expected: FAIL — `Scanner` not found (line_items still rendered as one input showing the JSON string).

- [ ] **Step 3: Add a `FieldBody` component and use it**

In `frontend/app/page.tsx`, add this component near the other helper components (e.g. just before `function Review(`):

```tsx
function FieldBody({f}:{f:any}){let rows:any[]|null=null;try{const p=JSON.parse(f.field_value);if(Array.isArray(p)&&p.length&&typeof p[0]==="object")rows=p}catch{}
 if(rows){const cols=Object.keys(rows[0]);return <div className="mt-2 overflow-x-auto rounded border border-slate-200 bg-white"><table className="w-full border-collapse text-xs"><thead><tr>{cols.map(c=><th key={c} className="border-b border-slate-200 px-2 py-1.5 text-left font-semibold uppercase tracking-wide text-slate-400">{c.replaceAll("_"," ")}</th>)}</tr></thead><tbody>{rows.map((r,i)=><tr key={i}>{cols.map(c=><td key={c} className="border-b border-slate-100 px-2 py-1.5 tabular-nums">{r[c]??"—"}</td>)}</tr>)}</tbody></table></div>}
 return <input className="mt-2 w-full rounded border border-slate-200 bg-white px-2 py-1.5 text-sm" defaultValue={f.field_value||""} onChange={e=>f.field_value=e.target.value}/>}
```

Then in the `Review` field map, replace the existing `<input …/>` (the one with `defaultValue={f.field_value||""}`) with:

```tsx
<FieldBody f={f}/>
```

(Leave the surrounding `<label>`, the grounded badge, the `%`, and the `source_quote` line exactly as they are.)

- [ ] **Step 4: Typecheck + full frontend suite**

Run: `cd frontend && npx tsc --noEmit && npm test`
Expected: `tsc` clean; all tests pass — the new `review-table` test plus the existing suite (their mock fields are scalar strings → `FieldBody` renders the input, unchanged).

- [ ] **Step 5: Commit**

```bash
git add frontend/app/page.tsx frontend/app/review-table.test.tsx
git commit -m "feat: render line-items as a readable table in the review panel

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 6: Manual verification (controller, after merge/deploy)**

Upload `Dummy_Invoice_AI_Demo.pdf`: `line_items` shows a 3-row table (Scanner/Printer/Installation) with **clean** amounts (`18,500`, not `I18,500`); `seller_gstin`=`29ABCDE1234F1Z5`, `buyer_gstin`=`27AACCA9876K1Z2`, `bill_to` populated; all grounded.

---

## Notes for the implementer

- Keep the dense one-line style in `page.tsx` / `document_schemas.py`.
- `json` and `re` are already imported in `services.py`.
- The array table is **read-only** (view), by design — no cell editing.
- Live-verify the real LLM array round-trip after deploy (integration), same precedent as other LLM-path changes.
