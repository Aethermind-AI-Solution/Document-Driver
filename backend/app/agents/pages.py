import fitz
from .. import services


def split_pages(data: bytes, suffix: str) -> list[dict]:
    """One entry per PDF page ({index, pdf_bytes, text}); images/bad PDFs → single page."""
    if suffix.lower() != ".pdf":
        return [{"index": 0, "pdf_bytes": data, "text": ""}]
    try:
        pages = []
        with fitz.open(stream=data, filetype="pdf") as doc:
            for i in range(doc.page_count):
                one = fitz.open()
                try:
                    one.insert_pdf(doc, from_page=i, to_page=i)
                    pages.append({"index": i, "pdf_bytes": one.tobytes(),
                                  "text": services._clean_text(doc[i].get_text())})
                finally:
                    one.close()
        return pages or [{"index": 0, "pdf_bytes": data, "text": ""}]
    except Exception:
        return [{"index": 0, "pdf_bytes": data, "text": ""}]


def render_png(pdf_bytes: bytes, dpi: int = 150) -> bytes:
    """First page of a PDF as PNG bytes. Non-PDF input (already an image) is
    returned unchanged so callers can pass either."""
    if pdf_bytes[:5] != b"%PDF-":
        return pdf_bytes
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        pix = doc[0].get_pixmap(dpi=dpi)
        return pix.tobytes("png")
