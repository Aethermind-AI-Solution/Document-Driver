"""Error-handling hardening: storage failures return a clean 502, and every error
response carries CORS headers so the browser surfaces the real status instead of
'Failed to fetch'."""
import io
import app.main as main_mod
from app import services

ORIGIN = "http://localhost:3000"  # in the default CORS_ORIGINS for tests


def _pdf():
    return {"file": ("a.pdf", io.BytesIO(b"%PDF-1.4 test"), "application/pdf")}


def test_upload_storage_failure_returns_502_with_cors(client, monkeypatch):
    class BadStore:
        def save(self, key, data):
            raise RuntimeError("supabase says no")

    monkeypatch.setattr(main_mod, "get_storage", lambda: BadStore())
    resp = client.post("/upload?document_type=invoice", files=_pdf(),
                       headers={"Origin": ORIGIN})
    assert resp.status_code == 502
    assert "storage" in resp.json()["detail"].lower()
    # the browser must be able to read this error → CORS header present
    assert resp.headers.get("access-control-allow-origin") == ORIGIN


def test_unhandled_error_returns_500_with_cors(client, monkeypatch):
    # force an unexpected (non-HTTPException) error deep in a handler
    def boom(db):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(main_mod, "available_schemas", boom)
    resp = client.get("/schemas", headers={"Origin": ORIGIN})
    assert resp.status_code == 500
    assert resp.json()["detail"] == "Internal server error"
    # without the catch-all + CORS ordering, this header would be missing (→ "Failed to fetch")
    assert resp.headers.get("access-control-allow-origin") == ORIGIN
