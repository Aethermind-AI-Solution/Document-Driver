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
