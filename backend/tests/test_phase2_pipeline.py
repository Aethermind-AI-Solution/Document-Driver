import asyncio
from app import services
from app.agents import pipeline, classifier
from app.models import Document, ExtractedField


def test_run_pipeline_end_to_end(db_session, monkeypatch):
    # offline stubs
    async def fake_classify(text, keys):
        return ("invoice", 0.99)
    monkeypatch.setattr(classifier, "classify_document", fake_classify)
    monkeypatch.setattr(services, "ai_extract", lambda text, fields, path: [
        {"field_name": f["name"], "field_value": ("100" if f["name"] == "total" else None),
         "source_quote": None, "grounded": ("grounded" if f["name"] == "total" else "absent"),
         "confidence": (0.95 if f["name"] == "total" else 0.55)} for f in fields])
    # single fake page (avoid real fitz)
    monkeypatch.setattr("app.agents.pipeline.split_pages",
                        lambda data, suffix: [{"index": 0, "pdf_bytes": b"", "text": "Total 100"}])
    monkeypatch.setattr("app.storage.get_storage",
                        lambda: type("S", (), {"open": lambda self, k: b"bytes"})())
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x.pdf")
    db_session.add(doc); db_session.commit(); db_session.refresh(doc)

    result = asyncio.run(pipeline.run_pipeline(db_session, doc, "invoice"))
    assert result.status in {"processed", "review_required"}
    assert result.pipeline_trace and {s["name"] for s in result.pipeline_trace} >= {
        "Classifier", "Extractor", "Reconciler", "Validator"}
    total = db_session.query(ExtractedField).filter_by(document_id=doc.id, field_name="total").first()
    assert total.field_value == "100" and total.original_value == "100"
