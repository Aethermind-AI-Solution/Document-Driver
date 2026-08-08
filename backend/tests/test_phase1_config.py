import pytest
from app import config


def test_check_production_config_raises_on_default_secret(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql+psycopg://user:pw@host/db")
    monkeypatch.setattr(config, "JWT_SECRET", "dev-insecure-secret-change-me")
    with pytest.raises(RuntimeError):
        config.check_production_config()


def test_check_production_config_allows_strong_secret(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql+psycopg://user:pw@host/db")
    monkeypatch.setattr(config, "JWT_SECRET", "a-strong-secret")
    config.check_production_config()


def test_check_production_config_allows_sqlite_with_default_secret(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite:///./database/document_intelligence.db")
    monkeypatch.setattr(config, "JWT_SECRET", "dev-insecure-secret-change-me")
    config.check_production_config()
