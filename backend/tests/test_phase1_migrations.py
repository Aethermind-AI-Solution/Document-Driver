from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

BACKEND = Path(__file__).resolve().parents[1]


def test_upgrade_head_builds_schema(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    tables = set(inspect(create_engine(url)).get_table_names())
    assert {"documents", "extracted_fields", "audit_logs", "schema_definitions"} <= tables
    cols = {c["name"] for c in inspect(create_engine(url)).get_columns("extracted_fields")}
    assert "original_value" in cols


def test_identity_migration_adds_users_and_actor(tmp_path):
    url = f"sqlite:///{tmp_path / 'i.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    insp = inspect(create_engine(url))
    assert "users" in insp.get_table_names()
    assert "actor_id" in {c["name"] for c in insp.get_columns("audit_logs")}
