from app import ocr, config


class _FakeTextract:
    def detect_document_text(self, Document):
        return {"Blocks": [
            {"BlockType": "WORD", "Text": "Acme",
             "Geometry": {"BoundingBox": {"Left": 0.1, "Top": 0.2, "Width": 0.1, "Height": 0.05}}},
            {"BlockType": "LINE", "Text": "ignored line"},
            {"BlockType": "WORD", "Text": "Supplies",
             "Geometry": {"BoundingBox": {"Left": 0.21, "Top": 0.2, "Width": 0.15, "Height": 0.05}}},
        ]}


def test_ocr_image_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(config, "OCR_BACKEND", "none")
    assert ocr.ocr_image(b"x") == {"text": "", "words": []}


def test_ocr_image_parses_words_and_text(monkeypatch):
    monkeypatch.setattr(config, "OCR_BACKEND", "textract")
    monkeypatch.setattr(ocr, "_textract_client", lambda: _FakeTextract())
    out = ocr.ocr_image(b"imgbytes")
    assert out["text"] == "Acme Supplies"
    assert out["words"][0] == {"text": "Acme", "box": [0.1, 0.2, 0.2, 0.25]}
    assert out["words"][1]["text"] == "Supplies"


def test_ocr_image_failsoft_on_error(monkeypatch):
    monkeypatch.setattr(config, "OCR_BACKEND", "textract")
    def _boom(): raise RuntimeError("aws down")
    monkeypatch.setattr(ocr, "_textract_client", _boom)
    assert ocr.ocr_image(b"x") == {"text": "", "words": []}
