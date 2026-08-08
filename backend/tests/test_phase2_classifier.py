import asyncio
from app import services
from app.agents.base import PipelineContext
from app.agents import classifier
from app.models import Document


def _ctx(db, hint="purchase_order"):
    doc = Document(filename="x.pdf", document_type=hint, stored_path="x.pdf")
    db.add(doc); db.commit(); db.refresh(doc)
    ctx = PipelineContext(db=db, document=doc, hint_type=hint)
    ctx.pages = [{"index": 0, "pdf_bytes": b"", "text": "Invoice No: INV-1"}]
    return ctx


def test_classifier_uses_llm_result(db_session, monkeypatch):
    monkeypatch.setattr(classifier, "classify_document",
                        lambda text, keys: ("invoice", 0.97))
    ctx = _ctx(db_session)
    asyncio.run(classifier.ClassifierAgent().run(ctx))
    assert ctx.document.document_type == "invoice"
    assert ctx.schema is not None and ctx.schema["fields"]


def test_classifier_falls_back_to_hint_on_low_conf(db_session, monkeypatch):
    monkeypatch.setattr(classifier, "classify_document",
                        lambda text, keys: ("invoice", 0.10))
    ctx = _ctx(db_session, hint="purchase_order")
    asyncio.run(classifier.ClassifierAgent().run(ctx))
    assert ctx.document.document_type == "purchase_order"


def test_classifier_falls_back_on_error(db_session, monkeypatch):
    def boom(text, keys):
        return (None, 0.0)
    monkeypatch.setattr(classifier, "classify_document", boom)
    ctx = _ctx(db_session, hint="invoice")
    asyncio.run(classifier.ClassifierAgent().run(ctx))
    assert ctx.document.document_type == "invoice"
