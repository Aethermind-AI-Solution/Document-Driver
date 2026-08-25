from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
BACKEND = Path(__file__).resolve().parents[1]

def test_migration_0009_org_backfill(tmp_path):
    url = f"sqlite:///{tmp_path / 'o.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "0008_document_delivery_flags")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("INSERT INTO users (email,password_hash,role,is_active,created_at) "
                       "VALUES ('a@b.co','x','admin',1,CURRENT_TIMESTAMP)"))
    command.upgrade(cfg, "head")
    insp = inspect(eng)
    assert "organizations" in insp.get_table_names()
    with eng.connect() as c:
        assert c.execute(text("SELECT id FROM organizations")).scalar() == 1
        assert c.execute(text("SELECT org_id FROM users WHERE email='a@b.co'")).scalar() == 1


def test_migration_0009_downgrade_upgrade_roundtrip(tmp_path):
    """0009 must round-trip cleanly (down past it, then back up) without erroring,
    covering both the schema_definitions unique-constraint swap and its reversal."""
    url = f"sqlite:///{tmp_path / 'r.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0008_document_delivery_flags")
    eng = create_engine(url)
    insp = inspect(eng)
    assert "organizations" not in insp.get_table_names()
    command.upgrade(cfg, "head")
    insp = inspect(eng)
    assert "organizations" in insp.get_table_names()
    uq_names = {uc["name"] for uc in insp.get_unique_constraints("schema_definitions")}
    assert "uq_schema_definitions_org_id_key" in uq_names
