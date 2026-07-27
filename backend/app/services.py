import base64, json, os, re, time
from pathlib import Path
import fitz
from sqlalchemy.orm import Session
from .config import GEMINI_MODEL, OPENAI_MODEL
from .document_schemas import SCHEMAS
from .models import AuditLog, Document, ExtractedField, SchemaDefinition

def log(db: Session, document_id: int, action: str, details: str = ""):
    db.add(AuditLog(document_id=document_id, action=action, details=details))

def resolve_review_action(action: str, reason: str | None) -> dict:
    """Map a review action to status/flag overrides and an audit entry.

    status/review_required of None mean "leave the document's value unchanged".
    """
    reason = (reason or "").strip() or None
    if action == "approve":
        return {"status": "approved", "review_required": False,
                "log_action": "Approved", "log_details": "Human review completed"}
    if action == "reject":
        return {"status": "rejected", "review_required": False,
                "log_action": "Rejected", "log_details": reason or "No reason given"}
    return {"status": None, "review_required": None,
            "log_action": "Edited", "log_details": reason or "Review values updated"}

def available_schemas(db: Session):
    builtins = [{"key": k, **v} for k, v in SCHEMAS.items()]
    custom = [{"key": s.key, "name": s.name, "fields": s.fields} for s in db.query(SchemaDefinition).all()]
    return builtins + custom

def schema_for(db: Session, key: str):
    if key in SCHEMAS: return SCHEMAS[key]
    schema = db.query(SchemaDefinition).filter_by(key=key).first()
    if not schema: raise ValueError(f"Unknown schema '{key}'")
    return {"name": schema.name, "fields": schema.fields}

def extract_text(path: str) -> str:
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        with fitz.open(p) as pdf: return "\n".join(page.get_text() for page in pdf)
    return ""

def fallback_extract(text: str, fields: list[dict]):
    pairs = {}
    labels = {f["name"]: f.get("label", f["name"]).lower() for f in fields}
    for name, label in labels.items():
        match = re.search(rf"{re.escape(label)}\s*[:#-]?\s*([^\n]+)", text, re.I)
        if match: pairs[name] = match.group(1).strip()
    patterns = {"invoice_number":r"(?:invoice|inv)\s*(?:no\.?|#)\s*[:#-]?\s*([A-Z0-9-]+)", "total":r"(?:grand\s*)?total\s*[:]?\s*[$₹€]?\s*([\d,.]+)", "currency":r"\b(USD|INR|EUR|GBP)\b"}
    for name, pattern in patterns.items():
        if name not in pairs and re.search(pattern, text, re.I): pairs[name] = re.search(pattern, text, re.I).group(1)
    data = {f["name"]: {"value": pairs.get(f["name"]), "quote": None} for f in fields}
    return _ground_fields(data, fields, text)

def passes_validation(field: dict, value: str | None) -> bool:
    """Apply schema-defined required, regex, and enum controls before human approval."""
    if field.get("required") and not value:
        return False
    if value and field.get("pattern") and not re.fullmatch(field["pattern"], value):
        return False
    if value and field.get("enum") and value not in field["enum"]:
        return False
    return True

def _norm(s):
    return re.sub(r"\s+", " ", (s or "").lower()).strip()

def _ground(value, quote, hay, verifiable):
    if value is None:
        return ("absent", 0.55)
    if not verifiable:
        return ("unverified", 0.70)
    if (quote and _norm(quote) in hay) or (_norm(value) in hay):
        return ("grounded", 0.95)
    # Fallback for reformatted / multi-value fields (e.g. flattened table rows like
    # line_items) whose exact string isn't contiguous in the doc text: if (nearly)
    # all of the value's tokens appear in the document, treat it as grounded.
    tokens = [t for t in re.findall(r"[a-z0-9]+", _norm(value)) if len(t) >= 2]
    if tokens and sum(1 for t in tokens if t in hay) / len(tokens) >= 0.85:
        return ("grounded", 0.90)
    return ("ungrounded", 0.40)

def _ground_fields(data, fields, doc_text):
    hay = _norm(doc_text)
    verifiable = bool(hay)
    d = data if isinstance(data, dict) else {}
    out = []
    for f in fields:
        entry = d.get(f["name"])
        value = entry.get("value") if isinstance(entry, dict) else entry
        quote = entry.get("quote") if isinstance(entry, dict) else None
        value = str(value) if value is not None and str(value).strip() else None
        quote = quote if isinstance(quote, str) and quote.strip() else None
        status, conf = _ground(value, quote, hay, verifiable)
        out.append({"field_name": f["name"], "field_value": value,
                    "source_quote": quote, "grounded": status, "confidence": conf})
    return out

def openai_extract(text: str, fields: list[dict], source_path: str):
    try:
        from openai import OpenAI
        client = OpenAI()
        properties = {f["name"]: {"type":"object","properties":{"value":{"type":["string","null"],"description":f.get("label",f["name"])},"quote":{"type":["string","null"],"description":"verbatim quote from the document this value was taken from"}},"required":["value","quote"],"additionalProperties":False} for f in fields}
        content = [{"type": "input_text", "text": "Extract all requested values. For each field also return a short verbatim quote from the document that the value was taken from. Use null for value and quote when a value is absent. Do not paraphrase the quote."}]
        path = Path(source_path)
        encoded = base64.b64encode(path.read_bytes()).decode()
        if path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            content.append({"type":"input_image", "image_url":f"data:image/{path.suffix[1:]};base64,{encoded}"})
        else:
            content.append({"type":"input_file", "filename":path.name, "file_data":f"data:application/pdf;base64,{encoded}"})
        if text: content.append({"type":"input_text", "text": "Extracted text for reference:\n" + text[:50000]})
        response = client.responses.create(model=OPENAI_MODEL, input=[{"role":"user","content":content}], text={"format":{"type":"json_schema","name":"document_extraction","strict":True,"schema":{"type":"object","properties":properties,"required":[f["name"] for f in fields],"additionalProperties":False}}})
        return _ground_fields(json.loads(response.output_text), fields, text)
    except Exception as exc:
        import sys
        print(f"[openai_extract] falling back to regex — OpenAI error: {exc!r}", file=sys.stderr, flush=True)
        return fallback_extract(text, fields)

def gemini_extract(text: str, fields: list[dict], source_path: str):
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        path = Path(source_path)
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(path.suffix.lower(), "application/pdf")
        properties = {f["name"]: types.Schema(type=types.Type.OBJECT, properties={"value": types.Schema(type=types.Type.STRING, nullable=True, description=f.get("label", f["name"])), "quote": types.Schema(type=types.Type.STRING, nullable=True, description="verbatim quote from the document this value was taken from")}) for f in fields}
        schema = types.Schema(type=types.Type.OBJECT, properties=properties)
        contents = ["Extract the requested fields. For each field return its value and a short verbatim quote from the document the value was taken from; use null when a value is absent; do not paraphrase the quote.",
                    types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)]
        if text:
            contents.append("Extracted text for reference:\n" + text[:50000])
        response = client.models.generate_content(
            model=GEMINI_MODEL, contents=contents,
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema))
        return _ground_fields(json.loads(response.text), fields, text)
    except Exception as exc:
        import sys
        print(f"[gemini_extract] falling back to regex — Gemini error: {exc!r}", file=sys.stderr, flush=True)
        return fallback_extract(text, fields)

def ai_extract(text: str, fields: list[dict], source_path: str):
    if os.getenv("GEMINI_API_KEY"):
        return gemini_extract(text, fields, source_path)
    if os.getenv("OPENAI_API_KEY"):
        return openai_extract(text, fields, source_path)
    return fallback_extract(text, fields)

def process_document(db: Session, document: Document):
    started = time.perf_counter(); document.status = "processing"; db.commit()
    try:
        schema = schema_for(db, document.document_type)
        values = ai_extract(extract_text(document.stored_path), schema["fields"], document.stored_path)
        db.query(ExtractedField).filter_by(document_id=document.id).delete()
        definitions = {field["name"]: field for field in schema["fields"]}
        for field in values:
            field["validated"] = passes_validation(definitions[field["field_name"]], field["field_value"])
            db.add(ExtractedField(document_id=document.id, **field))
        document.confidence = sum(v["confidence"] for v in values) / max(len(values), 1)
        document.review_required = any(v["confidence"] < .9 or not v["validated"] for v in values)
        document.status = "review_required" if document.review_required else "processed"
        document.processing_time = round(time.perf_counter() - started, 2)
        log(db, document.id, "Processed", f"Applied {document.document_type} schema")
        db.commit(); db.refresh(document); return document
    except Exception as exc:
        document.status = "error"; log(db, document.id, "Processing failed", str(exc)); db.commit(); raise
