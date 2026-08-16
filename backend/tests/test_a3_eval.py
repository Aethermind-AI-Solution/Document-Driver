from app import services


def test_document_confidence_min_over_required():
    schema = [{"name": "total", "required": True}, {"name": "notes"}]
    fields = [{"field_name": "total", "confidence": 0.4}, {"field_name": "notes", "confidence": 0.95}]
    assert services.document_confidence(fields, schema) == 0.4


def test_document_confidence_wrong_required_not_averaged_away():
    schema = [{"name": "total", "required": True}, {"name": "a"}, {"name": "b"}, {"name": "c"}]
    fields = [{"field_name": "total", "confidence": 0.4}] + \
             [{"field_name": n, "confidence": 0.95} for n in ("a", "b", "c")]
    assert services.document_confidence(fields, schema) == 0.4


def test_document_confidence_fallback_mean_when_no_required():
    schema = [{"name": "a"}, {"name": "b"}]
    fields = [{"field_name": "a", "confidence": 0.8}, {"field_name": "b", "confidence": 0.6}]
    assert abs(services.document_confidence(fields, schema) - 0.7) < 1e-9


def test_document_confidence_empty_is_one():
    assert services.document_confidence([], []) == 1.0
