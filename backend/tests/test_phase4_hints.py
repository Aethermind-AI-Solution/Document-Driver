from app.services import get_correction_hints
from app.models import Document, ExtractedField

FIELDS = [{"name": "total"}, {"name": "seller_name"}]


def _approved_correction(db, dtype, field, original, corrected, status="approved", edited=True):
    doc = Document(filename="x", document_type=dtype, stored_path="x", status=status)
    db.add(doc); db.flush()
    db.add(ExtractedField(document_id=doc.id, field_name=field, original_value=original,
                          field_value=corrected, edited_by_user=edited, confidence=0.9))
    db.commit()
    return doc


def test_hints_from_approved_changed_edits(db_session):
    _approved_correction(db_session, "invoice", "total", "1,00", "100")
    assert get_correction_hints(db_session, "invoice", FIELDS) == {"total": [("1,00", "100")]}


def test_excludes_non_approved(db_session):
    _approved_correction(db_session, "invoice", "total", "5", "50", status="review_required")
    assert get_correction_hints(db_session, "invoice", FIELDS) == {}


def test_excludes_unchanged_and_unedited(db_session):
    _approved_correction(db_session, "invoice", "total", "9", "9")               # unchanged
    _approved_correction(db_session, "invoice", "seller_name", "A", "B", edited=False)  # not edited
    assert get_correction_hints(db_session, "invoice", FIELDS) == {}


def test_excludes_other_type_and_unknown_field(db_session):
    _approved_correction(db_session, "purchase_order", "total", "1", "2")        # other type
    _approved_correction(db_session, "invoice", "mystery", "1", "2")             # field not in schema
    assert get_correction_hints(db_session, "invoice", FIELDS) == {}


def test_per_field_and_global_caps(db_session, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "LEARNING_MAX_HINTS_PER_FIELD", 2)
    monkeypatch.setattr(config, "LEARNING_MAX_HINTS", 3)
    for i in range(5):
        _approved_correction(db_session, "invoice", "total", f"a{i}", f"b{i}")
    for i in range(5):
        _approved_correction(db_session, "invoice", "seller_name", f"c{i}", f"d{i}")
    hints = get_correction_hints(db_session, "invoice", FIELDS)
    assert len(hints["total"]) == 2                       # per-field cap
    assert sum(len(v) for v in hints.values()) == 3       # global cap


def test_dedups_identical_pairs(db_session):
    _approved_correction(db_session, "invoice", "total", "1,00", "100")
    _approved_correction(db_session, "invoice", "total", "1,00", "100")
    assert get_correction_hints(db_session, "invoice", FIELDS) == {"total": [("1,00", "100")]}


import json as _json
import os as _os
import tempfile


def test_hint_block_empty_is_blank():
    from app.services import _hint_block
    assert _hint_block({}) == ""


def test_hint_block_lines_and_guard():
    from app.services import _hint_block
    block = _hint_block({"total": [("1,00", "100")]})
    assert 'total: model extracted "1,00" → correct value was "100"' in block
    assert "THIS document actually contains" in block


def test_openai_extract_injects_hint_block(monkeypatch):
    import app.services as svc
    import openai
    captured = {}

    class FakeResp:
        def __init__(self, text): self.output_text = text

    class FakeResponses:
        def create(self, model, input, text):
            captured["input"] = input
            keys = list(text["format"]["schema"]["properties"].keys())
            return FakeResp(_json.dumps({k: {"value": None, "quote": None} for k in keys}))

    class FakeClient:
        responses = FakeResponses()

    monkeypatch.setattr(openai, "OpenAI", lambda: FakeClient())
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False); tmp.write(b"%PDF"); tmp.close()
    try:
        svc.openai_extract("doc text", [{"name": "total", "label": "Total"}], tmp.name,
                           hints={"total": [("1,00", "100")]})
    finally:
        _os.unlink(tmp.name)
    texts = [c["text"] for c in captured["input"][0]["content"] if c.get("type") == "input_text"]
    assert any("correct value was" in t for t in texts)


def test_ai_extract_threads_hints(monkeypatch):
    import app.services as svc
    captured = {}
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(svc, "openai_extract",
                        lambda text, fields, path, hints=None: captured.update(h=hints) or [])
    svc.ai_extract("t", [{"name": "total"}], "/x.pdf", hints={"total": [("a", "b")]})
    assert captured["h"] == {"total": [("a", "b")]}
