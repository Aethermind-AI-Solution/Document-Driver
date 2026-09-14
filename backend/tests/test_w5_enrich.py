from app import ocr, config
from app.agents import pages as pages_mod

def test_enrich_pages_fills_thin_text(monkeypatch):
    monkeypatch.setattr(config, "OCR_BACKEND", "textract")
    monkeypatch.setattr(config, "OCR_TEXT_MIN_CHARS", 40)
    monkeypatch.setattr(pages_mod, "render_png", lambda b, dpi=150: b"png")
    monkeypatch.setattr(ocr, "render_png", lambda b, dpi=150: b"png", raising=False)
    monkeypatch.setattr(ocr, "ocr_image", lambda data: {"text": "OCR RECOVERED TEXT " * 5, "words": []})
    pages = [{"index": 0, "pdf_bytes": b"%PDF-...", "text": "short"}]
    out = ocr.enrich_pages(pages)
    assert out[0]["text"].startswith("OCR RECOVERED TEXT")

def test_enrich_pages_keeps_rich_text(monkeypatch):
    monkeypatch.setattr(config, "OCR_BACKEND", "textract")
    monkeypatch.setattr(config, "OCR_TEXT_MIN_CHARS", 40)
    rich = "x" * 200
    pages = [{"index": 0, "pdf_bytes": b"%PDF-...", "text": rich}]
    assert ocr.enrich_pages(pages)[0]["text"] == rich

def test_enrich_pages_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "OCR_BACKEND", "none")
    pages = [{"index": 0, "pdf_bytes": b"x", "text": ""}]
    assert ocr.enrich_pages(pages)[0]["text"] == ""
