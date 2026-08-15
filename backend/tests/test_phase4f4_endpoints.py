import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app import auth
from app.database import Base, get_db
from app.main import app
from app.models import User, SchemaDefinition, Document
from app import services


def _fields():
    return [{"name": "foo", "label": "Foo", "type": "string", "required": False}]


@pytest.fixture
def env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'e.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    doc = Document(filename="a.pdf", document_type="widget", stored_path="a", status="review_required")
    admin = User(email="admin@t.local", password_hash="x", role="admin", is_active=True)
    reviewer = User(email="rev@t.local", password_hash="x", role="reviewer", is_active=True)
    session.add_all([doc, admin, reviewer]); session.commit()
    draft = SchemaDefinition(key="widget", name="Widget", fields=_fields(),
                             status="suggested", origin_document_id=doc.id)
    session.add(draft); session.commit()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[auth.get_current_user] = lambda: admin
    yield {"session": session, "draft_id": draft.id, "admin": admin, "reviewer": reviewer}
    app.dependency_overrides.clear()
    session.close()


def test_list_suggested(env):
    r = TestClient(app).get("/schemas/suggested")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1 and body[0]["key"] == "widget" and body[0]["origin_document_id"]


def test_edit_schema_fields(env):
    new = {"name": "Widget X", "fields": [{"name": "bar", "label": "Bar", "type": "number", "required": True}]}
    r = TestClient(app).patch(f"/schemas/{env['draft_id']}", json=new)
    assert r.status_code == 200 and r.json()["name"] == "Widget X"
    assert r.json()["fields"][0]["name"] == "bar"


def test_edit_rejects_empty_fields(env):
    r = TestClient(app).patch(f"/schemas/{env['draft_id']}", json={"fields": []})
    assert r.status_code == 422


def test_edit_404(env):
    r = TestClient(app).patch("/schemas/9999", json={"name": "z"})
    assert r.status_code == 404


def test_approve_makes_visible_to_classifier(env):
    session = env["session"]
    assert "widget" not in [s["key"] for s in services.available_schemas(session)]
    r = TestClient(app).post(f"/schemas/{env['draft_id']}/approve")
    assert r.status_code == 200
    assert "widget" in [s["key"] for s in services.available_schemas(session)]


def test_approve_404(env):
    assert TestClient(app).post("/schemas/9999/approve").status_code == 404


def test_reject_deletes(env):
    r = TestClient(app).delete(f"/schemas/{env['draft_id']}")
    assert r.status_code == 204
    assert env["session"].get(SchemaDefinition, env["draft_id"]) is None


def test_reject_404(env):
    assert TestClient(app).delete("/schemas/9999").status_code == 404


def test_non_admin_forbidden(env):
    app.dependency_overrides[auth.get_current_user] = lambda: env["reviewer"]
    c = TestClient(app)
    assert c.get("/schemas/suggested").status_code == 403
    assert c.patch(f"/schemas/{env['draft_id']}", json={"name": "z"}).status_code == 403
    assert c.post(f"/schemas/{env['draft_id']}/approve").status_code == 403
    assert c.delete(f"/schemas/{env['draft_id']}").status_code == 403
