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
