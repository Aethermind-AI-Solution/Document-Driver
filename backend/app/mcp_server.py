import secrets
from types import SimpleNamespace
from . import config
from .services import available_schemas, schema_for, log
from .models import Document, ExtractedField


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
