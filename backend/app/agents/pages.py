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
                one.insert_pdf(doc, from_page=i, to_page=i)
                pages.append({"index": i, "pdf_bytes": one.tobytes(),
                              "text": services._clean_text(doc[i].get_text())})
                one.close()
        return pages or [{"index": 0, "pdf_bytes": data, "text": ""}]
    except Exception:
        return [{"index": 0, "pdf_bytes": data, "text": ""}]
