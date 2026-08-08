import json
import re
from .. import services
from ..models import Document, ExtractedField
from .base import PipelineContext, StageResult, timed_stage


def _num(s) -> float | None:
    if s is None:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", str(s))
    try:
        return float(cleaned) if cleaned not in ("", "-", ".") else None
    except ValueError:
        return None


def _value(fields: list[dict], name: str):
    for f in fields:
        if f["field_name"] == name:
            return f.get("field_value")
    return None


def validate(db, document, fields: list[dict], fields_def: list[dict]) -> tuple[list[dict], list[str]]:
    by_name = {f["name"]: f for f in fields_def}
    for f in fields:
        fdef = by_name.get(f["field_name"], {})
        f["validated"] = services.passes_validation(fdef, f.get("field_value"))

    anomalies: list[str] = []

    # arithmetic: sum(line_items.amount) ~= total
    total = _num(_value(fields, "total"))
    li = _value(fields, "line_items")
    if total is not None and li:
        try:
            rows = json.loads(li)
            summed = sum(_num(r.get("amount")) or 0 for r in rows if isinstance(r, dict))
            if rows and abs(summed - total) > 1.0:
                anomalies.append(f"Line-item amounts sum to {summed:g} but total is {total:g}")
        except (TypeError, ValueError):
            pass

    # arithmetic: subtotal + tax ~= total
    subtotal, tax = _num(_value(fields, "subtotal")), _num(_value(fields, "tax"))
    if total is not None and subtotal is not None and tax is not None:
        if abs((subtotal + tax) - total) > 1.0:
            anomalies.append(f"Subtotal {subtotal:g} + tax {tax:g} != total {total:g}")

    # duplicate: same invoice_number + seller_gstin on another document
    inv, gst = _value(fields, "invoice_number"), _value(fields, "seller_gstin")
    if inv and gst:
        sub_inv = db.query(ExtractedField.document_id).filter_by(field_name="invoice_number", field_value=inv)
        sub_gst = db.query(ExtractedField.document_id).filter_by(field_name="seller_gstin", field_value=gst)
        dup = (db.query(Document.id)
               .filter(Document.id.in_(sub_inv), Document.id.in_(sub_gst), Document.id != document.id)
               .first())
        if dup:
            anomalies.append(f"Possible duplicate of #{dup[0]}")

    return fields, anomalies


class ValidatorAgent:
    name = "Validator"

    async def run(self, ctx: PipelineContext) -> StageResult:
        async def body(c: PipelineContext):
            c.fields, c.anomalies = validate(c.db, c.document, c.fields, c.schema["fields"])
        result = await timed_stage(self.name, body, ctx)
        if result.status != "error":
            result.status = "attention" if ctx.anomalies else "ok"
            result.detail = f"{len(ctx.anomalies)} anomaly(ies)" if ctx.anomalies else "ok"
        return result
