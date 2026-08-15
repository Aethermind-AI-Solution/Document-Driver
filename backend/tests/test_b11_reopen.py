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
