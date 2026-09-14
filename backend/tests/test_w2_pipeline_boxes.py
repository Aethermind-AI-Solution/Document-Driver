from app.agents import pipeline

def test_compute_field_boxes_maps_from_first_page(monkeypatch):
    from app import ocr, boxes
    monkeypatch.setattr(ocr, "ocr_image", lambda data: {"text": "", "words": [
        {"text": "Acme", "box": [0.1, 0.2, 0.2, 0.25]}]})
    monkeypatch.setattr(pipeline, "render_png", lambda b, dpi=150: b"png", raising=False)
    fields = [{"field_name": "vendor_name", "field_value": "Acme"}]
    pages = [{"index": 0, "pdf_bytes": b"%PDF-", "text": ""}]
    result = pipeline.compute_field_boxes(fields, pages)
    assert result == {"vendor_name": [0.1, 0.2, 0.2, 0.25]}

def test_compute_field_boxes_empty_without_pages():
    assert pipeline.compute_field_boxes([{"field_name": "x", "field_value": "y"}], []) == {}
