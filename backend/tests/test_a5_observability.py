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
