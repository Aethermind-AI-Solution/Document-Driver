from app.goldens import load_golden_set

def test_load_golden_set_returns_labeled_docs():
    items = load_golden_set("tests/fixtures/golden_set.json")
    assert len(items) >= 2
    first = items[0]
    assert set(first) == {"doc", "document_type", "fields"}
    assert isinstance(first["fields"], dict) and first["fields"]
