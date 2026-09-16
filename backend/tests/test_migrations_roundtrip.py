"""Exercise every Alembic migration's upgrade AND downgrade against a scratch
SQLite DB. The rest of the suite builds the schema via Base.metadata.create_all,
so without this the migration up/down paths never actually run in CI."""
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _alembic(args: list[str], db_url: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": db_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND, env=env, capture_output=True, text=True,
    )


def test_migrations_upgrade_downgrade_roundtrip(tmp_path):
    url = f"sqlite:///{tmp_path / 'roundtrip.db'}"
    up = _alembic(["upgrade", "head"], url)
    assert up.returncode == 0, f"upgrade head failed:\n{up.stderr}"
    down = _alembic(["downgrade", "base"], url)
    assert down.returncode == 0, f"downgrade base failed:\n{down.stderr}"
    up2 = _alembic(["upgrade", "head"], url)
    assert up2.returncode == 0, f"re-upgrade head failed:\n{up2.stderr}"
