"""OCR via AWS Textract (boto3, already a dependency). Fail-soft: any error or a
disabled backend returns empty text/boxes so the pipeline never breaks. Boxes are
normalized [left, top, right, bottom] in 0..1."""
import logging
from . import config
from .agents.pages import render_png

_log = logging.getLogger("aethermind")


def _textract_client():
    import boto3
    return boto3.client("textract", region_name=config.AWS_REGION)


def ocr_image(data: bytes) -> dict:
    if config.OCR_BACKEND != "textract":
        return {"text": "", "words": []}
    try:
        resp = _textract_client().detect_document_text(Document={"Bytes": data})
    except Exception:
        _log.warning("ocr failed; returning empty result", exc_info=True)
        return {"text": "", "words": []}
    words = []
    for b in resp.get("Blocks", []):
        if b.get("BlockType") != "WORD":
            continue
        bb = b.get("Geometry", {}).get("BoundingBox")
        if not bb:
            continue
        x0, y0 = bb["Left"], bb["Top"]
        words.append({"text": b.get("Text", ""),
                      "box": [round(x0, 4), round(y0, 4),
                              round(x0 + bb["Width"], 4), round(y0 + bb["Height"], 4)]})
    return {"text": " ".join(w["text"] for w in words), "words": words}


def enrich_pages(pages: list[dict]) -> list[dict]:
    """Replace thin/absent PDF text layers with OCR text so scanned docs extract.
    No-op unless OCR is enabled. Only overwrites when OCR yields more text."""
    if config.OCR_BACKEND != "textract":
        return pages
    for p in pages:
        if len((p.get("text") or "").strip()) >= config.OCR_TEXT_MIN_CHARS:
            continue
        recovered = ocr_image(render_png(p["pdf_bytes"])).get("text", "")
        if len(recovered.strip()) > len((p.get("text") or "").strip()):
            p["text"] = recovered
    return pages
