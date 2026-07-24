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
