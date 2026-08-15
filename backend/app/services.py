import base64, json, os, re
from pathlib import Path
import fitz
from sqlalchemy.orm import Session
from .config import GEMINI_MODEL, OPENAI_MODEL
from . import config
from .document_schemas import SCHEMAS
from .auth import hash_password
from .models import AuditLog, Document, ExtractedField, SchemaDefinition, User

def log(db: Session, document_id: int, action: str, details: str = "", actor=None):
    db.add(AuditLog(document_id=document_id, action=action, details=details,
                    actor_id=getattr(actor, "id", None),
                    actor_email=getattr(actor, "email", None)))

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
    """Approved schemas only (builtins + status=='approved' rows). Suggested
    drafts are intentionally excluded from the classifier, the /schemas
    selector, and the MCP list_document_types tool."""
    builtins = [{"key": k, **v} for k, v in SCHEMAS.items()]
    custom = [{"key": s.key, "name": s.name, "fields": s.fields}
              for s in db.query(SchemaDefinition).filter_by(status="approved").all()]
    return builtins + custom


def all_schema_keys(db: Session) -> set[str]:
    """Every schema key — builtins plus every DB row regardless of status.
    Used for uniqueness checks (SchemaDefinition.key is UNIQUE)."""
    return set(SCHEMAS.keys()) | {k for (k,) in db.query(SchemaDefinition.key).all()}

def schema_for(db: Session, key: str):
    if key in SCHEMAS: return SCHEMAS[key]
    schema = db.query(SchemaDefinition).filter_by(key=key).first()
    if not schema: raise ValueError(f"Unknown schema '{key}'")
    return {"name": schema.name, "fields": schema.fields}

def _hint_block(hints: dict) -> str:
    if not hints:
        return ""
    lines = ["Human reviewers have previously corrected extractions for this document type. Learn the pattern:"]
    for name, pairs in hints.items():
        for original, corrected in pairs:
            lines.append(f'- {name}: model extracted "{original}" → correct value was "{corrected}"')
    lines.append("Apply the same judgment, but ALWAYS extract the value that THIS document actually contains — "
                 "never copy a past value that is not present in this document.")
    return "\n".join(lines)

def _clean_text(s):
    # PyMuPDF decodes the ₹ glyph as "I"; strip a ₹ or a word-boundary "I" that sits
    # immediately before a digit (e.g. "I18,500"→"18,500") without touching words.
    return re.sub(r"(?:₹|(?<![A-Za-z0-9])I)(?=\d)", "", s or "")

def extract_text(path: str) -> str:
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        with fitz.open(p) as pdf: return _clean_text("\n".join(page.get_text() for page in pdf))
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

def _contains(hay, needle):
    """Word-boundary phrase match so a short value ("1") doesn't match inside a
    larger token ("100"). Boundaries are on alphanumerics only."""
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", hay) is not None

def _ground(value, quote, hay, hay_tokens, verifiable):
    if value is None:
        return ("absent", 0.55)
    nvalue = _norm(value)
    if not nvalue:  # whitespace-only / a row of blank cells is not evidence of anything
        return ("absent", 0.55)
    if not verifiable:
        return ("unverified", 0.70)
    nquote = _norm(quote) if quote else ""
    if (nquote and _contains(hay, nquote)) or _contains(hay, nvalue):
        return ("grounded", 0.95)
    # Fallback for reformatted / multi-value fields (e.g. flattened table rows like
    # line_items) whose exact string isn't contiguous in the doc text: if (nearly)
    # all of the value's tokens appear as standalone tokens in the document, ground it.
    tokens = [t for t in re.findall(r"[a-z0-9]+", nvalue) if len(t) >= 2]
    if tokens and sum(1 for t in tokens if t in hay_tokens) / len(tokens) >= 0.85:
        return ("grounded", 0.90)
    return ("ungrounded", 0.40)

def _ground_fields(data, fields, doc_text):
    hay = _norm(doc_text)
    hay_tokens = set(re.findall(r"[a-z0-9]+", hay))
    verifiable = bool(hay)
    d = data if isinstance(data, dict) else {}
    out = []
    for f in fields:
        entry = d.get(f["name"])
        raw = entry.get("value") if isinstance(entry, dict) else entry
        quote = entry.get("quote") if isinstance(entry, dict) else None
        quote = quote if isinstance(quote, str) and quote.strip() else None
        if f.get("type") == "array" and f.get("columns"):
            rows = [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
            field_value = json.dumps(rows) if rows else None
            blob = " ".join(str(v) for r in rows for v in r.values() if v is not None and str(v).strip()) or None
            status, conf = _ground(blob, quote, hay, hay_tokens, verifiable)
        else:
            field_value = str(raw) if raw is not None and str(raw).strip() else None
            status, conf = _ground(field_value, quote, hay, hay_tokens, verifiable)
        out.append({"field_name": f["name"], "field_value": field_value,
                    "source_quote": quote, "grounded": status, "confidence": conf})
    return out

def _openai_prop(f):
    quote = {"type": ["string", "null"], "description": "verbatim quote from the document this value was taken from"}
    if f.get("type") == "array" and f.get("columns"):
        cols = f["columns"]
        item = {"type": "object", "properties": {c: {"type": ["string", "null"]} for c in cols},
                "required": cols, "additionalProperties": False}
        value = {"type": ["array", "null"], "description": f.get("label", f["name"]), "items": item}
    else:
        value = {"type": ["string", "null"], "description": f.get("label", f["name"])}
    return {"type": "object", "properties": {"value": value, "quote": quote},
            "required": ["value", "quote"], "additionalProperties": False}


def _gemini_prop(f):
    from google.genai import types
    quote = types.Schema(type=types.Type.STRING, nullable=True, description="verbatim quote from the document this value was taken from")
    if f.get("type") == "array" and f.get("columns"):
        item = types.Schema(type=types.Type.OBJECT, properties={c: types.Schema(type=types.Type.STRING, nullable=True) for c in f["columns"]})
        value = types.Schema(type=types.Type.ARRAY, nullable=True, items=item, description=f.get("label", f["name"]))
    else:
        value = types.Schema(type=types.Type.STRING, nullable=True, description=f.get("label", f["name"]))
    return types.Schema(type=types.Type.OBJECT, properties={"value": value, "quote": quote})


def openai_extract(text: str, fields: list[dict], source_path: str, hints: dict | None = None):
    try:
        from openai import OpenAI
        client = OpenAI()
        properties = {f["name"]: _openai_prop(f) for f in fields}
        content = [{"type": "input_text", "text": "Extract all requested values with a short verbatim quote from the document for each. For list/table fields, return value as an array of row objects using the given columns. Use null when a value is absent; do not paraphrase the quote."}]
        path = Path(source_path)
        encoded = base64.b64encode(path.read_bytes()).decode()
        if path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            content.append({"type":"input_image", "image_url":f"data:image/{path.suffix[1:]};base64,{encoded}"})
        else:
            content.append({"type":"input_file", "filename":path.name, "file_data":f"data:application/pdf;base64,{encoded}"})
        if text: content.append({"type":"input_text", "text": "Extracted text for reference:\n" + text[:50000]})
        block = _hint_block(hints or {})
        if block: content.append({"type": "input_text", "text": block})
        response = client.responses.create(model=OPENAI_MODEL, input=[{"role":"user","content":content}], text={"format":{"type":"json_schema","name":"document_extraction","strict":True,"schema":{"type":"object","properties":properties,"required":[f["name"] for f in fields],"additionalProperties":False}}})
        return _ground_fields(json.loads(response.output_text), fields, text)
    except Exception as exc:
        import sys
        print(f"[openai_extract] falling back to regex — OpenAI error: {exc!r}", file=sys.stderr, flush=True)
        return fallback_extract(text, fields)

def gemini_extract(text: str, fields: list[dict], source_path: str, hints: dict | None = None):
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        path = Path(source_path)
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(path.suffix.lower(), "application/pdf")
        properties = {f["name"]: _gemini_prop(f) for f in fields}
        schema = types.Schema(type=types.Type.OBJECT, properties=properties)
        contents = ["Extract the requested fields, each with a short verbatim quote from the document. For list/table fields, return value as an array of row objects using the columns. Use null when a value is absent; do not paraphrase the quote.",
                    types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)]
        if text:
            contents.append("Extracted text for reference:\n" + text[:50000])
        block = _hint_block(hints or {})
        if block: contents.append(block)
        response = client.models.generate_content(
            model=GEMINI_MODEL, contents=contents,
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema))
        return _ground_fields(json.loads(response.text), fields, text)
    except Exception as exc:
        import sys
        print(f"[gemini_extract] falling back to regex — Gemini error: {exc!r}", file=sys.stderr, flush=True)
        return fallback_extract(text, fields)

def ai_extract(text: str, fields: list[dict], source_path: str, hints: dict | None = None):
    if os.getenv("GEMINI_API_KEY"):
        return gemini_extract(text, fields, source_path, hints)
    if os.getenv("OPENAI_API_KEY"):
        return openai_extract(text, fields, source_path, hints)
    return fallback_extract(text, fields)

def process_document(db: Session, document: Document, actor=None) -> Document:
    """Sync entry point: runs the async agent pipeline to completion."""
    import asyncio
    from .agents.pipeline import run_pipeline
    return asyncio.run(run_pipeline(db, document, document.document_type, actor))

def create_user(db: Session, email: str, password: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password(password), role=role, is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    return user

def bootstrap_admin(db: Session) -> None:
    from .config import ADMIN_EMAIL, ADMIN_PASSWORD
    if not (ADMIN_EMAIL and ADMIN_PASSWORD):
        return
    if db.query(User).count() > 0:
        return
    create_user(db, ADMIN_EMAIL, ADMIN_PASSWORD, "admin")

def get_correction_hints(db: Session, document_type: str, fields: list[dict]) -> dict:
    """Recent human corrections on APPROVED docs of this type →
    {field_name: [(original_value, corrected_value), ...]}. Bounded, deduped;
    {} on cold-start or any error (never breaks extraction)."""
    try:
        names = {f["name"] for f in fields}
        rows = (db.query(ExtractedField.field_name, ExtractedField.original_value, ExtractedField.field_value)
                .join(Document, ExtractedField.document_id == Document.id)
                .filter(Document.document_type == document_type,
                        Document.status == "approved",
                        ExtractedField.edited_by_user.is_(True),
                        ExtractedField.original_value.isnot(None),
                        ExtractedField.field_value.isnot(None),
                        ExtractedField.original_value != ExtractedField.field_value)
                .order_by(Document.upload_date.desc(), ExtractedField.id.desc())
                .all())
        # Group rows by field name, preserving order from query
        rows_by_field = {}
        for name, original, corrected in rows:
            if name in names:
                if name not in rows_by_field:
                    rows_by_field[name] = []
                rows_by_field[name].append((original, corrected))

        hints: dict = {}
        seen: set = set()
        total = 0
        # Iterate through fields in order, respecting caps
        for f in fields:
            name = f["name"]
            if name not in rows_by_field:
                continue
            for original, corrected in rows_by_field[name]:
                if total >= config.LEARNING_MAX_HINTS:
                    break
                key = (name, original, corrected)
                if key in seen:
                    continue
                if name not in hints:
                    hints[name] = []
                if len(hints[name]) >= config.LEARNING_MAX_HINTS_PER_FIELD:
                    continue
                hints[name].append((original, corrected))
                seen.add(key)
                total += 1
            if total >= config.LEARNING_MAX_HINTS:
                break
        return hints
    except Exception as exc:
        import sys
        print(f"[get_correction_hints] skipping hints — error: {exc!r}", file=sys.stderr, flush=True)
        return {}
