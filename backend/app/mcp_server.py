import secrets
import base64
import binascii
from types import SimpleNamespace
from datetime import datetime, timezone
from pathlib import Path
from . import config
from . import storage
from .services import available_schemas, schema_for, log
from .models import Document, ExtractedField
from .agents.pipeline import run_pipeline


def build_service_principal() -> SimpleNamespace:
    """The fixed identity all MCP tool calls act as (for audit stamping)."""
    return SimpleNamespace(id=None, email="mcp-service", role=config.MCP_SERVICE_ROLE)


class TokenAuthASGI:
    """ASGI middleware: require a Bearer token on HTTP requests before delegating."""
    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send); return
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode()
        presented = auth[7:] if auth.startswith("Bearer ") else ""
        if not (presented and secrets.compare_digest(presented, self.token)):
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
            return
        await self.app(scope, receive, send)


def list_types_impl(db) -> list[dict]:
    return [{"key": s["key"], "name": s["name"]} for s in available_schemas(db)]


def _doc_result(doc: "Document") -> dict:
    return {
        "document_id": doc.id, "status": doc.status, "confidence": doc.confidence,
        "document_type": doc.document_type,
        "fields": [{"field_name": f.field_name, "field_value": f.field_value,
                    "confidence": f.confidence, "grounded": f.grounded}
                   for f in doc.extracted_fields],
        "anomalies": doc.anomalies, "pipeline_trace": doc.pipeline_trace,
    }


def get_document_impl(db, document_id: int) -> dict:
    doc = db.get(Document, document_id)
    if not doc:
        raise ValueError(f"Document {document_id} not found")
    return _doc_result(doc)


def submit_correction_impl(db, document_id: int, field_name: str, value: str, actor) -> dict:
    field = db.query(ExtractedField).filter_by(document_id=document_id, field_name=field_name).first()
    if not field:
        raise ValueError(f"Field '{field_name}' not found on document {document_id}")
    field.field_value = value
    field.edited_by_user = True
    log(db, document_id, "Edited", f"MCP correction: {field_name}", actor=actor)
    db.commit()
    return {"field_name": field.field_name, "field_value": field.field_value,
            "edited_by_user": field.edited_by_user}


_ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg"}


async def extract_document_impl(db, file_base64: str, filename: str,
                                document_type: str, actor) -> dict:
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise ValueError(f"Unsupported file type '{suffix}' (allowed: PDF, PNG, JPEG)")
    try:
        data = base64.b64decode(file_base64, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("file_base64 is not valid base64")
    hint = "invoice" if document_type == "auto" else document_type
    schema_for(db, hint)  # raises ValueError on unknown type
    key = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{Path(filename).name}"
    storage.get_storage().save(key, data)
    doc = Document(filename=filename, document_type=hint, stored_path=key)
    db.add(doc); db.commit(); db.refresh(doc)
    await run_pipeline(db, doc, doc.document_type, actor=actor)
    db.refresh(doc)
    return _doc_result(doc)
