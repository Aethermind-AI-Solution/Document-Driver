"""Map extracted field values to Textract word boxes by token match. Boxes are
normalized [x0,y0,x1,y1]; a field's box is the union of the word boxes whose text
is one of the value's tokens."""
import re
from .services import _norm


def _tokens(value: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", _norm(value)) if t}


def _union(bs: list[list[float]]) -> list[float]:
    return [min(b[0] for b in bs), min(b[1] for b in bs),
            max(b[2] for b in bs), max(b[3] for b in bs)]


def map_field_boxes(fields: list[dict], words: list[dict]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for f in fields:
        val = f.get("field_value")
        if not val or not str(val).strip():
            continue
        toks = _tokens(str(val))
        if not toks:
            continue
        matched = [w["box"] for w in words if any(t in toks for t in _tokens(w["text"])) and w.get("box")]
        if matched:
            out[f["field_name"]] = [round(c, 4) for c in _union(matched)]
    return out
