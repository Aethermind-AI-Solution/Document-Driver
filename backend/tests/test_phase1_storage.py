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
