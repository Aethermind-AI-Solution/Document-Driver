import fitz
from app.agents.pages import render_png

def _one_page_pdf() -> bytes:
    doc = fitz.open(); doc.new_page(); data = doc.tobytes(); doc.close(); return data

def test_render_png_of_pdf_returns_png():
    out = render_png(_one_page_pdf())
    assert out[:8] == b"\x89PNG\r\n\x1a\n"

def test_render_png_passthrough_for_non_pdf():
    png = b"\x89PNG\r\n\x1a\nfake"
    assert render_png(png) == png
