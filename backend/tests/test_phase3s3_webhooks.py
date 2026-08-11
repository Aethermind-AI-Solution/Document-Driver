from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

BACKEND = Path(__file__).resolve().parents[1]


def test_migration_adds_webhook_configs(tmp_path):
    url = f"sqlite:///{tmp_path / 'w.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    tables = set(inspect(create_engine(url)).get_table_names())
    assert "webhook_configs" in tables


from app import auth
from app.main import app
from app.models import User, WebhookConfig


def _as(db, role):
    u = User(email=f"{role}@x.co", password_hash="x", role=role, is_active=True)
    db.add(u); db.commit()
    app.dependency_overrides[auth.get_current_user] = lambda: u
    return u


def test_create_list_and_secret_hidden(client, db_session):
    r = client.post("/webhooks", json={"document_type": "invoice", "url": "https://h/x", "secret": "s3cr3t"})
    assert r.status_code == 201
    body = r.json()
    assert body["document_type"] == "invoice" and body["has_secret"] is True
    assert "secret" not in body                      # secret never returned
    listed = client.get("/webhooks").json()
    assert any(w["document_type"] == "invoice" for w in listed)


def test_duplicate_type_409(client, db_session):
    client.post("/webhooks", json={"document_type": "invoice", "url": "https://h/x"})
    dup = client.post("/webhooks", json={"document_type": "invoice", "url": "https://h/y"})
    assert dup.status_code == 409


def test_patch_and_delete(client, db_session):
    wid = client.post("/webhooks", json={"document_type": "invoice", "url": "https://h/x"}).json()["id"]
    assert client.patch(f"/webhooks/{wid}", json={"active": False}).json()["active"] is False
    assert client.delete(f"/webhooks/{wid}").status_code == 204
    assert client.patch(f"/webhooks/{wid}", json={"active": True}).status_code == 404


def test_non_admin_forbidden(client, db_session):
    _as(db_session, "reviewer")
    try:
        assert client.get("/webhooks").status_code == 403
        assert client.post("/webhooks", json={"document_type": "x", "url": "https://h"}).status_code == 403
    finally:
        app.dependency_overrides.pop(auth.get_current_user, None)
