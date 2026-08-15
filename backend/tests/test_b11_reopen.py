from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from app import services

BACKEND = Path(__file__).resolve().parents[1]


def test_migration_0006_adds_revision(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    cols = {c["name"] for c in inspect(create_engine(url)).get_columns("documents")}
    assert "revision" in cols


def test_can_transition_allows_reopen_cycle():
    assert services.can_transition("approved", "reopened")
    assert services.can_transition("rejected", "reopened")
    assert services.can_transition("reopened", "approved")
    assert services.can_transition("reopened", "rejected")
    assert services.can_transition("review_required", "approved")


def test_can_transition_blocks_invalid():
    assert not services.can_transition("approved", "approved")
    assert not services.can_transition("approved", "rejected")
    assert not services.can_transition("rejected", "approved")
    assert not services.can_transition("processing", "approved")
    assert not services.can_transition("nonsense", "approved")


import pytest
from app.models import Document
import app.main as main_mod


def _doc(db, status):
    d = Document(filename="a.pdf", document_type="invoice", stored_path="p", status=status,
                 review_required=(status == "review_required"))
    db.add(d); db.commit(); db.refresh(d)
    return d


@pytest.mark.parametrize("status", ["approved", "rejected", "reopened", "processing"])
def test_process_blocked_on_non_reprocessable(client, db_session, monkeypatch, status):
    calls = []
    monkeypatch.setattr(main_mod, "run_pipeline_task", lambda *a, **k: calls.append(a))
    d = _doc(db_session, status)
    r = client.post(f"/process/{d.id}")
    assert r.status_code == 409
    assert calls == []                       # pipeline never dispatched
    db_session.refresh(d)
    assert d.status == status                 # status untouched


@pytest.mark.parametrize("status", ["uploaded", "error", "review_required", "processed"])
def test_process_allowed_on_reprocessable(client, db_session, monkeypatch, status):
    calls = []
    monkeypatch.setattr(main_mod, "run_pipeline_task", lambda *a, **k: calls.append(a))
    d = _doc(db_session, status)
    r = client.post(f"/process/{d.id}")
    assert r.status_code == 202
    db_session.refresh(d)
    assert d.status == "processing"


from app.models import ExtractedField, AuditLog, User
from app import auth
from app.database import get_db


def _reviewer(db):
    u = User(email="rev@t.local", password_hash="x", role="reviewer", is_active=True)
    db.add(u); db.commit(); db.refresh(u); return u


def _as(user):
    main_mod.app.dependency_overrides[auth.get_current_user] = lambda: user


def test_reopen_approved_by_admin_with_reason(client, db_session):
    d = _doc(db_session, "approved")
    db_session.add(ExtractedField(document_id=d.id, field_name="total", field_value="100",
                                  original_value="100", confidence=0.9)); db_session.commit()
    r = client.put(f"/document/{d.id}", json={"fields": [], "action": "reopen", "reason": "wrong total"})
    assert r.status_code == 200
    db_session.refresh(d)
    assert d.status == "reopened" and d.review_required is True
    # fields preserved
    assert db_session.query(ExtractedField).filter_by(document_id=d.id).count() == 1
    assert db_session.query(AuditLog).filter_by(document_id=d.id, action="Reopened").count() == 1


def test_reopen_rejected_by_reviewer(client, db_session):
    rev = _reviewer(db_session); _as(rev)
    try:
        d = _doc(db_session, "rejected")
        r = client.put(f"/document/{d.id}", json={"fields": [], "action": "reopen"})
        assert r.status_code == 200
        db_session.refresh(d); assert d.status == "reopened"
    finally:
        main_mod.app.dependency_overrides.pop(auth.get_current_user, None)


def test_reopen_approved_by_reviewer_forbidden(client, db_session):
    rev = _reviewer(db_session); _as(rev)
    try:
        d = _doc(db_session, "approved")
        r = client.put(f"/document/{d.id}", json={"fields": [], "action": "reopen", "reason": "x"})
        assert r.status_code == 403
    finally:
        main_mod.app.dependency_overrides.pop(auth.get_current_user, None)


def test_reopen_approved_without_reason_422(client, db_session):
    d = _doc(db_session, "approved")
    r = client.put(f"/document/{d.id}", json={"fields": [], "action": "reopen"})
    assert r.status_code == 422


def test_reopen_non_terminal_conflict(client, db_session):
    d = _doc(db_session, "review_required")
    r = client.put(f"/document/{d.id}", json={"fields": [], "action": "reopen"})
    assert r.status_code == 409


def test_approve_on_approved_conflicts(client, db_session):
    d = _doc(db_session, "approved")
    r = client.put(f"/document/{d.id}", json={"fields": [], "action": "approve"})
    assert r.status_code == 409           # must reopen first
