from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

BACKEND = Path(__file__).resolve().parents[1]


def test_migration_0007_creates_table(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    assert "auto_approve_configs" in inspect(create_engine(url)).get_table_names()


def test_migration_0008_adds_document_columns(tmp_path):
    url = f"sqlite:///{tmp_path / 'm2.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    cols = {c["name"] for c in inspect(create_engine(url)).get_columns("documents")}
    assert {"webhook_status", "webhook_detail", "auto_approved"} <= cols


import pytest
from app import services
from app.models import Document


def _doc(db, status="review_required"):
    d = Document(filename="a.pdf", document_type="invoice", stored_path="p",
                 status=status, review_required=(status == "review_required"), confidence=0.95)
    db.add(d); db.commit(); db.refresh(d)
    return d


def test_apply_approval_sets_state_and_audits(db_session):
    d = _doc(db_session)
    services.apply_approval(db_session, d, prior_status="review_required", actor=None,
                            action_label="Auto-approved", details="conf 0.95")
    db_session.commit(); db_session.refresh(d)
    assert d.status == "approved" and d.review_required is False and d.revision == 1
    from app.models import AuditLog
    assert db_session.query(AuditLog).filter_by(document_id=d.id, action="Auto-approved").count() == 1


def test_apply_approval_rejects_illegal_transition(db_session):
    d = _doc(db_session, status="uploaded")
    with pytest.raises(ValueError):
        services.apply_approval(db_session, d, prior_status="uploaded", actor=None)


def test_human_approve_still_works(client, db_session):
    d = _doc(db_session)
    r = client.put(f"/document/{d.id}", json={"fields": [], "action": "approve"})
    assert r.status_code == 200
    db_session.refresh(d)
    assert d.status == "approved" and d.revision == 1
