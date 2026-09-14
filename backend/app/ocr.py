"""OCR via AWS Textract (boto3, already a dependency). Fail-soft: any error or a
disabled backend returns empty text/boxes so the pipeline never breaks. Boxes are
normalized [left, top, right, bottom] in 0..1."""
import logging
from . import config

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
