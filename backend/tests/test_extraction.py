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


def test_grounded_via_token_overlap_for_reformatted_table():
    # a flattened line-items value whose rows are in the doc but spaced/ordered differently
    doc = "Industrial Barcode Scanner 2  18,500  37,000  Warehouse Label Printer 1  24,000"
    val = "Industrial Barcode Scanner 2 18,500 37,000Warehouse Label Printer 1 24,000"  # no space before 'Warehouse'
    out = _ground_fields({"line_items": {"value": val, "quote": None}},
                         [{"name": "line_items", "label": "Line Items"}], doc)
    assert out[0]["grounded"] == "grounded" and out[0]["confidence"] == 0.90


def test_ungrounded_when_tokens_absent():
    out = _ground_fields({"total": {"value": "Zebra Q9 Hyperdrive Unit", "quote": "Zebra Q9 Hyperdrive Unit"}},
                         [{"name": "total", "label": "Total"}], "Total: 100")
    assert out[0]["grounded"] == "ungrounded" and out[0]["confidence"] == 0.40


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


def test_ground_fields_array_without_columns_is_scalar():
    fields = [{"name": "line_items", "label": "Line Items", "type": "array"}]  # no columns
    out = _ground_fields({"line_items": {"value": "Item A; Item B", "quote": None}}, fields, "Item A; Item B")
    assert out[0]["field_value"] == "Item A; Item B" and out[0]["grounded"] == "grounded"
