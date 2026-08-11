import asyncio
from app import services
from app.agents.base import PipelineContext
from app.agents import extractor
from app.agents.pages import split_pages
from app.models import Document

FIELDS = [{"name": "total", "label": "Total"}]


def _ctx(db):
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x.pdf")
    db.add(doc); db.commit(); db.refresh(doc)
    ctx = PipelineContext(db=db, document=doc, hint_type="invoice")
    ctx.schema = {"name": "Invoice", "fields": FIELDS}
    ctx.pages = [{"index": i, "pdf_bytes": b"p", "text": f"page {i}"} for i in range(3)]
    return ctx


def test_extractor_fans_out_over_pages(db_session, monkeypatch):
    calls = []
    def fake(text, fields, path, hints=None):
        calls.append(text)
        return [{"field_name": "total", "field_value": "1", "source_quote": None,
                 "grounded": "grounded", "confidence": 0.95}]
    monkeypatch.setattr(services, "ai_extract", fake)
    ctx = _ctx(db_session)
    asyncio.run(extractor.ExtractorAgent().run(ctx))
    assert len(ctx.page_results) == 3
    assert all(r[0]["field_name"] == "total" for r in ctx.page_results)


def test_extractor_page_error_yields_absent(db_session, monkeypatch):
    def flaky(text, fields, path, hints=None):
        if "page 1" in text:
            raise RuntimeError("boom")
        return [{"field_name": "total", "field_value": "1", "source_quote": None,
                 "grounded": "grounded", "confidence": 0.95}]
    monkeypatch.setattr(services, "ai_extract", flaky)
    ctx = _ctx(db_session)
    asyncio.run(extractor.ExtractorAgent().run(ctx))
    assert len(ctx.page_results) == 3
    # pages 0 and 2 are intact
    assert ctx.page_results[0][0]["field_value"] == "1" and ctx.page_results[0][0]["grounded"] == "grounded"
    assert ctx.page_results[2][0]["field_value"] == "1" and ctx.page_results[2][0]["grounded"] == "grounded"
    # the failed page's field is absent
    page1 = ctx.page_results[1]
    assert page1[0]["field_value"] is None and page1[0]["grounded"] == "absent"


def test_split_pages_bad_pdf_is_single_page():
    pages = split_pages(b"not-a-pdf", ".pdf")
    assert len(pages) == 1 and pages[0]["index"] == 0 and pages[0]["text"] == ""


def test_split_pages_image_is_single_page():
    pages = split_pages(b"\x89PNG-bytes", ".png")
    assert len(pages) == 1 and pages[0]["index"] == 0 and pages[0]["text"] == ""
