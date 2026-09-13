from app.models import Document

def test_document_has_fingerprint_column():
    assert "fingerprint" in Document.__table__.columns
    assert Document.__table__.columns["fingerprint"].nullable is True
