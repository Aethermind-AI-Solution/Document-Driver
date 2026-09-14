from app import config
from app.agents import pipeline

def test_compute_field_boxes_maps_from_first_page(monkeypatch):
    from app import ocr, boxes
    monkeypatch.setattr(config, "OCR_BACKEND", "textract")
    monkeypatch.setattr(ocr, "ocr_image", lambda data: {"text": "", "words": [
        {"text": "Acme", "box": [0.1, 0.2, 0.2, 0.25]}]})
    monkeypatch.setattr(pipeline, "render_png", lambda b, dpi=150: b"png", raising=False)
    fields = [{"field_name": "vendor_name", "field_value": "Acme"}]
    pages = [{"index": 0, "pdf_bytes": b"%PDF-", "text": ""}]
    result = pipeline.compute_field_boxes(fields, pages)
    assert result == {"vendor_name": [0.1, 0.2, 0.2, 0.25]}

def test_compute_field_boxes_empty_without_pages():
    assert pipeline.compute_field_boxes([{"field_name": "x", "field_value": "y"}], []) == {}

def test_compute_field_boxes_disabled_by_default_does_not_call_render_png(monkeypatch):
    monkeypatch.setattr(config, "OCR_BACKEND", "none")
    def _boom(*a, **k):
        raise AssertionError("should not be called")
    monkeypatch.setattr(pipeline, "render_png", _boom, raising=False)
    fields = [{"field_name": "vendor_name", "field_value": "Acme"}]
    pages = [{"index": 0, "pdf_bytes": b"%PDF-", "text": ""}]
    result = pipeline.compute_field_boxes(fields, pages)
    assert result == {}

def test_compute_field_boxes_fail_soft_when_ocr_raises(monkeypatch):
    monkeypatch.setattr(config, "OCR_BACKEND", "textract")
    def _boom(pdf_bytes):
        raise RuntimeError("ocr backend exploded")
    monkeypatch.setattr(pipeline, "services_ocr_words", _boom)
    fields = [{"field_name": "vendor_name", "field_value": "Acme"}]
    pages = [{"index": 0, "pdf_bytes": b"%PDF-", "text": ""}]
    result = pipeline.compute_field_boxes(fields, pages)
    assert result == {}
