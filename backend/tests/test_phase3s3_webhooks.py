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


import hmac
import json as _json
from hashlib import sha256
from app import webhooks
from app.models import Document, ExtractedField, AuditLog


def test_sign_matches_hmac():
    body = b'{"a":1}'
    assert webhooks.sign(body, "k") == "sha256=" + hmac.new(b"k", body, sha256).hexdigest()


def test_build_payload_shape(db_session):
    doc = Document(filename="a.pdf", document_type="invoice", stored_path="a",
                   status="approved", confidence=0.9)
    db_session.add(doc); db_session.flush()
    db_session.add(ExtractedField(document_id=doc.id, field_name="total", field_value="100",
                                  original_value="100", confidence=0.95, grounded="grounded"))
    db_session.commit(); db_session.refresh(doc)
    p = webhooks.build_payload(doc, "you@x.co")
    assert p["event"] == "document.approved" and p["approved_by"] == "you@x.co"
    assert p["fields"][0]["field_value"] == "100"


def _seed_config_and_doc(db, secret=None, active=True):
    from app.models import WebhookConfig
    cfg = WebhookConfig(document_type="invoice", url="https://hook/x", secret=secret, active=active)
    doc = Document(filename="a.pdf", document_type="invoice", stored_path="a", status="approved")
    db.add_all([cfg, doc]); db.commit(); db.refresh(cfg); db.refresh(doc)
    return cfg, doc


def test_deliver_posts_signed_and_audits_success(db_session, monkeypatch):
    cfg, doc = _seed_config_and_doc(db_session, secret="s3cr3t")
    cfg_id, doc_id, org_id = cfg.id, doc.id, cfg.org_id
    captured = {}

    class FakeResp:
        status_code = 200

    def fake_post(url, data=None, headers=None, timeout=None):
        captured.update(url=url, data=data, headers=headers)
        return FakeResp()

    monkeypatch.setattr(webhooks, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(webhooks.requests, "post", fake_post)
    webhooks.deliver_webhook(cfg_id, doc_id, "you@x.co", org_id)
    assert captured["url"] == "https://hook/x"
    assert captured["headers"]["X-Aethermind-Signature"] == webhooks.sign(captured["data"], "s3cr3t")
    assert db_session.query(AuditLog).filter_by(document_id=doc_id, action="Webhook delivered").count() == 1


def test_deliver_no_secret_no_signature(db_session, monkeypatch):
    cfg, doc = _seed_config_and_doc(db_session, secret=None)
    cfg_id, doc_id, org_id = cfg.id, doc.id, cfg.org_id
    captured = {}
    monkeypatch.setattr(webhooks, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(webhooks.requests, "post",
                        lambda url, data=None, headers=None, timeout=None: captured.update(headers=headers) or type("R", (), {"status_code": 200})())
    webhooks.deliver_webhook(cfg_id, doc_id, "you@x.co", org_id)
    assert "X-Aethermind-Signature" not in captured["headers"]


def test_deliver_failure_audits_and_never_raises(db_session, monkeypatch):
    cfg, doc = _seed_config_and_doc(db_session)
    cfg_id, doc_id, org_id = cfg.id, doc.id, cfg.org_id
    monkeypatch.setattr(webhooks, "SessionLocal", lambda: db_session)
    def boom(*a, **k): raise RuntimeError("conn refused")
    monkeypatch.setattr(webhooks.requests, "post", boom)
    webhooks.deliver_webhook(cfg_id, doc_id, "you@x.co", org_id)   # must not raise
    assert db_session.query(AuditLog).filter_by(document_id=doc_id, action="Webhook failed").count() == 1


def test_deliver_inactive_config_no_post(db_session, monkeypatch):
    cfg, doc = _seed_config_and_doc(db_session, active=False)
    cfg_id, doc_id, org_id = cfg.id, doc.id, cfg.org_id
    posted = {"v": False}
    monkeypatch.setattr(webhooks, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(webhooks.requests, "post",
                        lambda *a, **k: posted.__setitem__("v", True) or type("R", (), {"status_code": 200})())
    webhooks.deliver_webhook(cfg_id, doc_id, "you@x.co", org_id)
    assert posted["v"] is False


import app.main as main_mod
from app.models import Document as _Doc, WebhookConfig as _WC


def _approve(client, doc_id):
    return client.put(f"/document/{doc_id}", json={"fields": [], "action": "approve"})


def test_approve_with_active_config_schedules_delivery(client, db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(main_mod, "deliver_webhook",
                        lambda config_id, document_id, approved_by, org_id=None: calls.append((config_id, document_id)))
    doc = _Doc(filename="a.pdf", document_type="invoice", stored_path="a", status="review_required")
    cfg = _WC(document_type="invoice", url="https://h/x", active=True)
    db_session.add_all([doc, cfg]); db_session.commit(); db_session.refresh(doc); db_session.refresh(cfg)
    assert _approve(client, doc.id).status_code == 200
    assert calls == [(cfg.id, doc.id)]


def test_approve_without_config_no_delivery(client, db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(main_mod, "deliver_webhook", lambda *a, **k: calls.append(a))
    doc = _Doc(filename="a.pdf", document_type="invoice", stored_path="a", status="review_required")
    db_session.add(doc); db_session.commit(); db_session.refresh(doc)
    _approve(client, doc.id)
    assert calls == []


def test_save_action_no_delivery(client, db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(main_mod, "deliver_webhook", lambda *a, **k: calls.append(a))
    doc = _Doc(filename="a.pdf", document_type="invoice", stored_path="a", status="review_required")
    cfg = _WC(document_type="invoice", url="https://h/x", active=True)
    db_session.add_all([doc, cfg]); db_session.commit(); db_session.refresh(doc)
    client.put(f"/document/{doc.id}", json={"fields": [], "action": "save"})
    assert calls == []
