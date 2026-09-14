import fitz
from app.models import Document
from app import storage

def _pdf_bytes():
    d = fitz.open(); d.new_page(); b = d.tobytes(); d.close(); return b

def test_image_endpoint_returns_png(client, db_session, monkeypatch):
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="key1", org_id=1)
    db_session.add(doc); db_session.commit()
    monkeypatch.setattr(storage, "get_storage", lambda: type("S", (), {"open": staticmethod(lambda k: _pdf_bytes())})())
    r = client.get(f"/document/{doc.id}/image")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

def test_image_endpoint_404_for_missing(client):
    assert client.get("/document/999999/image").status_code == 404
