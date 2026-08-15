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
