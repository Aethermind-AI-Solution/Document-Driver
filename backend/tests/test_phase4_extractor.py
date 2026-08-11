import asyncio
from app import services, config
from app.agents.base import PipelineContext
from app.agents import extractor
from app.models import Document


def _ctx(db):
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x")
    db.add(doc); db.commit(); db.refresh(doc)
    ctx = PipelineContext(db=db, document=doc, hint_type="invoice")
    ctx.schema = {"name": "Invoice", "fields": [{"name": "total", "label": "Total"}]}
    ctx.pages = [{"index": 0, "pdf_bytes": b"p", "text": "t"}]
    return ctx


def test_extractor_fetches_and_passes_hints(db_session, monkeypatch):
    monkeypatch.setattr(config, "LEARNING_ENABLED", True)
    monkeypatch.setattr(services, "get_correction_hints", lambda db, dt, fields: {"total": [("a", "b")]})
    seen = {}

    def fake_ai(text, fields, path, hints=None):
        seen["hints"] = hints
        return [{"field_name": "total", "field_value": "1", "source_quote": None,
                 "grounded": "grounded", "confidence": 0.9}]

    monkeypatch.setattr(services, "ai_extract", fake_ai)
    ctx = _ctx(db_session)
    res = asyncio.run(extractor.ExtractorAgent().run(ctx))
    assert seen["hints"] == {"total": [("a", "b")]}
    assert "correction hints" in res.detail


def test_extractor_skips_hints_when_disabled(db_session, monkeypatch):
    monkeypatch.setattr(config, "LEARNING_ENABLED", False)
    called = {"v": False}
    monkeypatch.setattr(services, "get_correction_hints",
                        lambda *a, **k: called.__setitem__("v", True) or {})
    seen = {}
    monkeypatch.setattr(services, "ai_extract",
                        lambda text, fields, path, hints=None: seen.update(hints=hints) or
                        [{"field_name": "total", "field_value": "1", "source_quote": None,
                          "grounded": "grounded", "confidence": 0.9}])
    ctx = _ctx(db_session)
    res = asyncio.run(extractor.ExtractorAgent().run(ctx))
    assert called["v"] is False and seen["hints"] == {}
    assert "correction hints" not in res.detail
