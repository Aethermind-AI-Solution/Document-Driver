import pytest
from app.storage import LocalStorage


def test_local_storage_round_trip(tmp_path):
    store = LocalStorage(tmp_path)
    key = store.save("2026_inv.pdf", b"%PDF-1.4 bytes")
    assert key == "2026_inv.pdf"
    assert store.open(key) == b"%PDF-1.4 bytes"


def test_local_storage_delete(tmp_path):
    store = LocalStorage(tmp_path)
    store.save("x.pdf", b"data")
    store.delete("x.pdf")
    with pytest.raises(FileNotFoundError):
        store.open("x.pdf")


def test_get_storage_returns_local_by_default(monkeypatch, tmp_path):
    from app import config, storage
    monkeypatch.setattr(config, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path)
    s = storage.get_storage()
    assert isinstance(s, LocalStorage)


def test_get_storage_s3_fast_fail_missing_config(monkeypatch):
    from app import config, storage
    monkeypatch.setattr(config, "STORAGE_BACKEND", "s3")
    monkeypatch.setattr(config, "R2_ENDPOINT", "")
    monkeypatch.setattr(config, "R2_BUCKET", "")
    monkeypatch.setattr(config, "R2_ACCESS_KEY_ID", "")
    monkeypatch.setattr(config, "R2_SECRET_ACCESS_KEY", "")
    with pytest.raises(RuntimeError):
        storage.get_storage()


def test_process_reads_bytes_through_storage(db_session, monkeypatch, tmp_path):
    from app import config, services
    from app.models import Document
    monkeypatch.setattr(config, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path)
    # a doc whose bytes live only in storage under its key
    from app.storage import LocalStorage
    LocalStorage(tmp_path).save("k.pdf", b"dummy-pdf-bytes")
    captured = {}
    monkeypatch.setattr(services, "extract_text", lambda path: (captured.__setitem__("path", path), "Total 500")[1])
    monkeypatch.setattr(services, "ai_extract", lambda text, fields, path: [
        {"field_name": "total", "field_value": "500", "source_quote": None,
         "grounded": "grounded", "confidence": 0.95}])
    doc = Document(filename="k.pdf", document_type="invoice", stored_path="k.pdf")
    db_session.add(doc); db_session.commit(); db_session.refresh(doc)
    services.process_document(db_session, doc)
    # extract_text received a real temp file path (not the storage key)
    assert captured["path"].endswith(".pdf") and captured["path"] != "k.pdf"


def test_s3_storage_uses_region_and_path_style(monkeypatch):
    import boto3
    from app.storage import S3Storage
    captured = {}

    def fake_client(service, **kwargs):
        captured["service"] = service
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(boto3, "client", fake_client)
    S3Storage("https://ref.supabase.co/storage/v1/s3", "aethermind", "ak", "sk", region="us-east-1")
    assert captured["service"] == "s3"
    assert captured["region_name"] == "us-east-1"
    assert captured["endpoint_url"] == "https://ref.supabase.co/storage/v1/s3"
    cfg = captured["config"]
    assert cfg.signature_version == "s3v4"
    assert cfg.s3["addressing_style"] == "path"
