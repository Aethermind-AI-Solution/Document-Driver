from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
import pytest
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import SchemaDefinition
from app import services

BACKEND = Path(__file__).resolve().parents[1]


def test_0005_adds_schema_status_columns(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    cols = {c["name"] for c in inspect(create_engine(url)).get_columns("schema_definitions")}
    assert {"status", "origin_document_id", "created_at"} <= cols


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'svc.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _fields():
    return [{"name": "foo", "label": "Foo", "type": "string", "required": False}]


def test_available_schemas_hides_suggested_but_schema_for_resolves(db):
    db.add(SchemaDefinition(key="widget", name="Widget", fields=_fields(), status="suggested",
                            origin_document_id=1))
    db.commit()
    keys = [s["key"] for s in services.available_schemas(db)]
    assert "widget" not in keys                       # hidden from classifier/selector
    assert services.schema_for(db, "widget")["name"] == "Widget"   # still resolvable


def test_available_schemas_shows_approved(db):
    db.add(SchemaDefinition(key="gadget", name="Gadget", fields=_fields(), status="approved"))
    db.commit()
    assert "gadget" in [s["key"] for s in services.available_schemas(db)]


def test_all_schema_keys_includes_suggested_and_builtins(db):
    db.add(SchemaDefinition(key="widget", name="Widget", fields=_fields(), status="suggested",
                            origin_document_id=1))
    db.commit()
    keys = services.all_schema_keys(db)
    assert "widget" in keys and "invoice" in keys


from fastapi.testclient import TestClient
from app import auth
from app.database import get_db
from app.main import app
from app.models import User


def test_post_schema_409_on_suggested_key_collision(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    session.add(SchemaDefinition(key="widget", name="Widget", fields=_fields(),
                                 status="suggested", origin_document_id=1))
    session.commit()
    admin = User(email="a@t.local", password_hash="x", role="admin", is_active=True)
    session.add(admin); session.commit()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[auth.get_current_user] = lambda: admin
    try:
        r = TestClient(app).post("/schemas", json={"key": "widget", "name": "W2",
                                                   "fields": _fields()})
        assert r.status_code == 409
    finally:
        app.dependency_overrides.clear()
        session.close()
