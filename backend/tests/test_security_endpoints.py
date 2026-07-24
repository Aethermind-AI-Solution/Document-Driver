import io
from app import config
from app.security import rate_limiter


def _file():
    return {"file": ("a.pdf", io.BytesIO(b"%PDF-1.4 minimal"), "application/pdf")}


def test_gate_blocks_documents_without_token(client, db_session, monkeypatch):
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "secret")
    assert client.get("/documents").status_code == 401


def test_gate_allows_documents_with_token(client, db_session, monkeypatch):
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "secret")
    assert client.get("/documents", headers={"X-Access-Token": "secret"}).status_code == 200


def test_health_open_without_token(client, db_session, monkeypatch):
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "secret")
    assert client.get("/health").status_code == 200


def test_upload_size_limit(client, db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "")
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 0)      # any non-empty file is too big
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path)
    rate_limiter.reset()
    files = {"file": ("big.pdf", io.BytesIO(b"x" * 1024), "application/pdf")}
    assert client.post("/upload?document_type=invoice", files=files).status_code == 413


def test_rate_limit_blocks_after_max(client, db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "")
    monkeypatch.setattr(config, "RATE_LIMIT_MAX", 2)
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path)
    rate_limiter.reset()
    assert client.post("/upload?document_type=invoice", files=_file()).status_code == 201
    assert client.post("/upload?document_type=invoice", files=_file()).status_code == 201
    assert client.post("/upload?document_type=invoice", files=_file()).status_code == 429
