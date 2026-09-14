from app.boxes import map_field_boxes

WORDS = [
    {"text": "Acme", "box": [0.10, 0.20, 0.20, 0.25]},
    {"text": "Supplies", "box": [0.21, 0.20, 0.36, 0.25]},
    {"text": "INV-1042", "box": [0.60, 0.10, 0.75, 0.14]},
]

def test_maps_multiword_value_to_union_box():
    fields = [{"field_name": "vendor_name", "field_value": "Acme Supplies"}]
    out = map_field_boxes(fields, WORDS)
    assert out["vendor_name"] == [0.10, 0.20, 0.36, 0.25]

def test_maps_single_token_value():
    fields = [{"field_name": "invoice_number", "field_value": "INV-1042"}]
    assert map_field_boxes(fields, WORDS)["invoice_number"] == [0.60, 0.10, 0.75, 0.14]

def test_omits_unmatched_and_empty():
    fields = [{"field_name": "po_number", "field_value": "PO-999"},
              {"field_name": "notes", "field_value": None}]
    assert map_field_boxes(fields, WORDS) == {}
