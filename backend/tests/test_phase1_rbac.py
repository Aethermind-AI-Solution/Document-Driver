import io
from app import auth
from app.models import Document, ExtractedField
from app.main import app


def _as(db, role):
    from app.models import User
    u = User(email=f"{role}@x.co", password_hash="x", role=role, is_active=True)
    db.add(u); db.commit()
    app.dependency_overrides[auth.get_current_user] = lambda: u
    return u


def test_viewer_cannot_upload(client, db_session):
    _as(db_session, "viewer")
    r = client.post("/upload", files={"file": ("a.pdf", io.BytesIO(b"%PDF"), "application/pdf")})
    assert r.status_code == 403


def test_viewer_can_read_documents(client, db_session):
    _as(db_session, "viewer")
    assert client.get("/documents").status_code == 200


def test_edit_stamps_actor_in_audit(client, db_session):
    actor = _as(db_session, "reviewer")
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x.pdf")
    db_session.add(doc); db_session.flush()
    db_session.add(ExtractedField(document_id=doc.id, field_name="total",
                                  field_value="1", original_value="1", confidence=0.9))
    db_session.commit()
    r = client.put(f"/document/{doc.id}", json={"fields": [], "action": "approve"})
    assert r.status_code == 200
    assert any(a["actor_email"] == "reviewer@x.co" for a in r.json()["audit"])
