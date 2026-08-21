import json
import logging
import sys
from app.logging_config import JsonFormatter, configure_logging
from app import config


def test_json_formatter_basic():
    rec = logging.LogRecord("aethermind", logging.INFO, __file__, 1, "hello", None, None)
    out = json.loads(JsonFormatter().format(rec))
    assert out["level"] == "INFO" and out["logger"] == "aethermind"
    assert out["msg"] == "hello" and "ts" in out


def test_json_formatter_extra_and_exc():
    rec = logging.LogRecord("aethermind", logging.INFO, __file__, 1, "m", None, None)
    rec.document_id = 7
    rec.stage = "pipeline"
    out = json.loads(JsonFormatter().format(rec))
    assert out["document_id"] == 7 and out["stage"] == "pipeline"
    try:
        raise ValueError("boom")
    except ValueError:
        rec2 = logging.LogRecord("aethermind", logging.ERROR, __file__, 1, "err", None, sys.exc_info())
    out2 = json.loads(JsonFormatter().format(rec2))
    assert "boom" in out2["exc"]


def test_configure_logging_idempotent(monkeypatch):
    monkeypatch.setattr(config, "LOG_FORMAT", "json")
    configure_logging()
    configure_logging()
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_aethermind", False)]
    assert len(ours) == 1


def test_configure_logging_plain(monkeypatch):
    monkeypatch.setattr(config, "LOG_FORMAT", "plain")
    configure_logging()
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_aethermind", False)]
    assert len(ours) == 1 and not isinstance(ours[0].formatter, JsonFormatter)


import logging as _logging
from app.agents import pipeline
from app.models import Document, AutoApproveConfig, User
from app import config as _config, services


class _Capture(_logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_auto_approve_emits_structured_log(db_session, monkeypatch):
    monkeypatch.setattr(_config, "AUTO_APPROVE_ENABLED", True)
    doc = Document(filename="a.pdf", document_type="invoice", stored_path="p",
                   status="processed", review_required=False, confidence=0.97)
    db_session.add(doc)
    db_session.add(AutoApproveConfig(document_type="invoice", enabled=True, min_confidence=0.95))
    db_session.commit(); db_session.refresh(doc)
    monkeypatch.setattr(pipeline, "SessionLocal", lambda: db_session, raising=False)

    # Set up logging capture - use a clean handler to ensure we see all logs
    cap = _Capture()
    cap.setLevel(_logging.DEBUG)  # Ensure the handler accepts DEBUG messages
    lg = _logging.getLogger("aethermind")

    # Save old state
    old_level = lg.level if lg.level != _logging.NOTSET else None
    old_disabled = lg.disabled

    # Add handler and ensure level is set
    lg.addHandler(cap)
    old_level_actual = lg.level
    lg.setLevel(_logging.DEBUG)
    lg.disabled = False  # Ensure logger is enabled

    try:
        pipeline._maybe_auto_approve(db_session, doc)
    finally:
        # Restore old state
        lg.removeHandler(cap)
        if old_level_actual != _logging.NOTSET:
            lg.setLevel(old_level_actual)
        else:
            lg.setLevel(_logging.NOTSET)
        lg.disabled = old_disabled

    # Check for the log record
    hit = [r for r in cap.records if getattr(r, "document_id", None) == doc.id
           and "auto" in r.getMessage().lower()]
    assert hit, f"expected a structured auto-approve log record carrying document_id. Got {len(cap.records)} records: {[r.getMessage() for r in cap.records]}"


from datetime import datetime, timezone, timedelta
from app.models import AuditLog


def test_percentile():
    from app.main import _percentile
    assert _percentile([], 50) is None
    assert _percentile([10], 95) == 10
    assert _percentile([10, 20, 30, 40], 50) == 20      # nearest-rank
    assert _percentile([10, 20, 30, 40], 95) == 40


def test_admin_metrics_shape_and_admin_only(client, db_session):
    # seed a mix of statuses
    for st in ["processed", "approved", "error", "review_required"]:
        db_session.add(Document(filename=f"{st}.pdf", document_type="invoice", stored_path="p", status=st))
    db_session.commit()
    err = db_session.query(Document).filter_by(status="error").first()
    db_session.add(AuditLog(document_id=err.id, action="Processing failed", details="boom"))
    # a stuck processing doc (old upload_date) + a fresh one
    old = Document(filename="stuck.pdf", document_type="invoice", stored_path="p", status="processing",
                   upload_date=datetime.now(timezone.utc) - timedelta(hours=2))
    fresh = Document(filename="fresh.pdf", document_type="invoice", stored_path="p", status="processing",
                     upload_date=datetime.now(timezone.utc))
    # a doc with a pipeline_trace for latency
    traced = Document(filename="t.pdf", document_type="invoice", stored_path="p", status="processed",
                      pipeline_trace=[{"name": "Classifier", "status": "ok", "detail": "", "duration_ms": 100},
                                      {"name": "Extractor", "status": "ok", "detail": "", "duration_ms": 300}])
    db_session.add_all([old, fresh, traced]); db_session.commit()

    r = client.get("/admin/metrics")
    assert r.status_code == 200
    body = r.json()
    assert body["status_counts"]["error"] == 1 and body["status_counts"]["processing"] == 2
    assert any(e["detail"] == "boom" for e in body["errors_recent"])
    assert body["stuck_processing"]["count"] == 1                       # only the 2h-old one
    stages = {s["stage"]: s for s in body["stage_latency"]}
    assert stages["Classifier"]["p50_ms"] == 100 and stages["Extractor"]["n"] == 1


def test_admin_metrics_forbidden_for_reviewer(client, db_session, monkeypatch):
    from app import auth
    import app.main as main_mod
    rev = User(email="rev@t.local", password_hash="x", role="reviewer", is_active=True)
    db_session.add(rev); db_session.commit()
    main_mod.app.dependency_overrides[auth.get_current_user] = lambda: rev
    try:
        assert client.get("/admin/metrics").status_code == 403
    finally:
        main_mod.app.dependency_overrides.pop(auth.get_current_user, None)
