"""Phase 0 quick wins: original-value capture (Q2), CSV line-item flatten (Q4),
schema-payload validation (Q5), and SQLite pragmas (Q1)."""
from app.models import Document, ExtractedField


# --- Q2: store the original AI value separately from the human-corrected value ---

def test_process_captures_original_value(db_session, monkeypatch):
    from app import services, storage
    from app.agents import classifier as agent_classifier

    async def fake_classify(text, keys):
        return ("invoice", 0.99)
    monkeypatch.setattr(agent_classifier, "classify_document", fake_classify)
    monkeypatch.setattr(services, "extract_text", lambda path: "Total 500")
    monkeypatch.setattr(services, "ai_extract", lambda text, fields, path, hints=None: [
        {"field_name": "total", "field_value": "500", "source_quote": None,
         "grounded": "grounded", "confidence": 0.95},
    ])
    monkeypatch.setattr("app.agents.pipeline.split_pages",
                        lambda data, suffix: [{"index": 0, "pdf_bytes": b"", "text": "Total 500"}])
    # Mock storage to return dummy PDF bytes
    mock_storage = lambda: type('obj', (object,), {'open': lambda self, key: b"dummy-pdf-bytes"})()
    monkeypatch.setattr(storage, "get_storage", mock_storage)
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x.pdf")
    db_session.add(doc); db_session.commit(); db_session.refresh(doc)
    services.process_document(db_session, doc)
    field = db_session.query(ExtractedField).filter_by(document_id=doc.id, field_name="total").first()
    assert field.field_value == "500" and field.original_value == "500"


def test_edit_preserves_original_value(client, db_session):
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="/x.pdf")
    db_session.add(doc); db_session.flush()
    db_session.add(ExtractedField(document_id=doc.id, field_name="total",
                                  field_value="500", original_value="500", confidence=0.9))
    db_session.commit()
    resp = client.put(f"/document/{doc.id}", json={
        "fields": [{"field_name": "total", "field_value": "600", "validated": True}],
        "action": "save"})
    assert resp.status_code == 200
    field = next(f for f in resp.json()["fields"] if f["field_name"] == "total")
    assert field["field_value"] == "600"
    assert field["original_value"] == "500"       # AI value preserved through the edit
    assert field["edited_by_user"] is True


# --- Q4: CSV export flattens line_items into real columns ---

def test_csv_flattens_line_items():
    from app.main import _to_csv
    fields = [
        {"field_name": "seller", "field_value": "Acme", "confidence": 0.95, "validated": True},
        {"field_name": "line_items",
         "field_value": '[{"description":"Widget","quantity":"2","amount":"100"}]',
         "confidence": 0.9, "validated": True},
    ]
    out = _to_csv(fields)
    assert "Acme" in out                          # scalar rows still present
    assert "# line_items" in out                  # a labelled section for the table
    assert "description,quantity,amount" in out    # column header
    assert "Widget,2,100" in out                  # flattened row


def test_csv_scalar_only_unchanged():
    from app.main import _to_csv
    out = _to_csv([{"field_name": "total", "field_value": "500", "confidence": 0.9, "validated": True}])
    assert "total,500,0.9,True" in out


# --- Q5: schema-payload validation rejects malformed field definitions ---

def test_create_schema_rejects_field_without_label(client):
    resp = client.post("/schemas", json={"key": "po_a", "name": "PO A", "fields": [{"name": "x"}]})
    assert resp.status_code == 422


def test_create_schema_rejects_empty_fields(client):
    resp = client.post("/schemas", json={"key": "po_b", "name": "PO B", "fields": []})
    assert resp.status_code == 422


def test_create_schema_accepts_valid(client):
    resp = client.post("/schemas", json={
        "key": "po_c", "name": "PO C",
        "fields": [{"name": "supplier", "label": "Supplier", "required": True}]})
    assert resp.status_code == 201
    assert resp.json()["key"] == "po_c"


# --- Q1: SQLite gets WAL + a busy timeout so concurrent writers don't 500 ---

def test_sqlite_pragmas_enabled(tmp_path):
    from sqlalchemy import create_engine, text
    from app.database import enable_sqlite_pragmas
    engine = create_engine(f"sqlite:///{tmp_path / 'p.db'}")
    enable_sqlite_pragmas(engine)
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000
