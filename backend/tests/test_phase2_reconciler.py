import json
from app.agents.reconciler import reconcile

FIELDS = [{"name": "total", "label": "Total"},
          {"name": "line_items", "label": "Line Items", "type": "array",
           "columns": ["description", "amount"]}]


def _f(name, value, grounded, conf):
    return {"field_name": name, "field_value": value, "source_quote": None,
            "grounded": grounded, "confidence": conf}


def test_reconcile_scalar_prefers_grounded():
    page0 = [_f("total", "100", "ungrounded", 0.40), _f("line_items", None, "absent", 0.55)]
    page1 = [_f("total", "999", "grounded", 0.95), _f("line_items", None, "absent", 0.55)]
    out = {o["field_name"]: o for o in reconcile([page0, page1], FIELDS)}
    assert out["total"]["field_value"] == "999" and out["total"]["grounded"] == "grounded"


def test_reconcile_concatenates_array_rows_in_page_order():
    r0 = _f("line_items", json.dumps([{"description": "A", "amount": "1"}]), "grounded", 0.90)
    r1 = _f("line_items", json.dumps([{"description": "B", "amount": "2"}]), "grounded", 0.90)
    page0 = [_f("total", None, "absent", 0.55), r0]
    page1 = [_f("total", None, "absent", 0.55), r1]
    out = {o["field_name"]: o for o in reconcile([page0, page1], FIELDS)}
    rows = json.loads(out["line_items"]["field_value"])
    assert [x["description"] for x in rows] == ["A", "B"]
