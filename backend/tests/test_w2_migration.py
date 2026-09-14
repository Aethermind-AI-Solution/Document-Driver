from app.models import ExtractedField

def test_extracted_field_has_box_column():
    col = ExtractedField.__table__.columns.get("box")
    assert col is not None and col.nullable is True
