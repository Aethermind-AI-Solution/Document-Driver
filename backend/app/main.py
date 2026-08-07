import csv, io, json, shutil
from datetime import datetime, timezone
from pathlib import Path
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from .config import CORS_ORIGINS
from . import config
from .security import rate_limit, require_access
from .database import Base, engine, get_db
from .models import AuditLog, Document, ExtractedField, SchemaDefinition
from .schemas import DocumentUpdate, SchemaPayload
from .services import available_schemas, log, process_document, resolve_review_action, schema_for
from .storage import get_storage

# Tests/dev create the schema directly; prod runs Alembic migrations on deploy.
if config.DATABASE_URL.startswith("sqlite"):
    Base.metadata.create_all(bind=engine)
app = FastAPI(title="Document Intelligence Engine", version="1.0.0", dependencies=[Depends(require_access)])
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _parse_rows(value):
    """Return a list-of-row-dicts if the field value is a JSON table (line_items),
    else None."""
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    if isinstance(parsed, list) and parsed and all(isinstance(r, dict) for r in parsed):
        return parsed
    return None

def _to_csv(fields: list[dict]) -> str:
    """Scalar fields as rows; table fields (line_items) flattened into a labelled
    section with real columns so ERP/AP consumers get columns, not a JSON blob."""
    stream = io.StringIO(); writer = csv.writer(stream)
    writer.writerow(["field", "value", "confidence", "validated"])
    tables = []
    for f in fields:
        rows = _parse_rows(f["field_value"])
        if rows is not None:
            writer.writerow([f["field_name"], f"{len(rows)} rows", f["confidence"], f["validated"]])
            tables.append((f["field_name"], rows))
        else:
            writer.writerow([f["field_name"], f["field_value"], f["confidence"], f["validated"]])
    for name, rows in tables:
        cols = list(rows[0].keys())
        writer.writerow([]); writer.writerow([f"# {name}"]); writer.writerow(cols)
        for r in rows:
            writer.writerow([r.get(c) for c in cols])
    return stream.getvalue()

def serialize(d: Document):
    return {"id":d.id,"filename":d.filename,"document_type":d.document_type,"upload_date":d.upload_date,"status":d.status,"processing_time":d.processing_time,"confidence":d.confidence,"review_required":d.review_required,"fields":[{"id":f.id,"field_name":f.field_name,"field_value":f.field_value,"original_value":f.original_value,"confidence":f.confidence,"validated":f.validated,"edited_by_user":f.edited_by_user,"source_quote":f.source_quote,"grounded":f.grounded} for f in d.extracted_fields],"audit":[{"action":a.action,"timestamp":a.timestamp,"details":a.details} for a in d.audit_logs]}

@app.get("/health")
def health(): return {"status":"ok"}

@app.get("/schemas")
def list_schemas(db: Session = Depends(get_db)): return available_schemas(db)

@app.post("/schemas", status_code=201)
def create_schema(payload: SchemaPayload, db: Session = Depends(get_db)):
    if payload.key in [s["key"] for s in available_schemas(db)]: raise HTTPException(409, "Schema key already exists")
    item = SchemaDefinition(**payload.model_dump()); db.add(item); db.commit(); return {"key":item.key,"name":item.name,"fields":item.fields}

@app.post("/upload", status_code=201, dependencies=[Depends(rate_limit)])
async def upload(file: UploadFile = File(...), document_type: str = "invoice", db: Session = Depends(get_db)):
    if Path(file.filename or "").suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg"}: raise HTTPException(400, "Only PDF, PNG, and JPEG are supported")
    try: schema_for(db, document_type)
    except ValueError as exc: raise HTTPException(400, str(exc))
    data = await file.read()
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024: raise HTTPException(413, f"File exceeds the {config.MAX_UPLOAD_MB} MB limit")
    safe_name = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{Path(file.filename).name}"
    key = get_storage().save(safe_name, data)
    doc = Document(filename=file.filename or safe_name, document_type=document_type, stored_path=key)
    db.add(doc); db.flush(); log(db, doc.id, "Uploaded", f"Schema selected: {document_type}"); db.commit(); db.refresh(doc)
    return serialize(doc)

@app.post("/process/{document_id}", dependencies=[Depends(rate_limit)])
def process(document_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc: raise HTTPException(404, "Document not found")
    try: return serialize(process_document(db, doc))
    except Exception as exc: raise HTTPException(500, f"Processing failed: {exc}")

@app.get("/documents")
def documents(q: str = "", status: str = "", db: Session = Depends(get_db)):
    query = db.query(Document)
    if q: query = query.filter(Document.filename.ilike(f"%{q}%"))
    if status: query = query.filter(Document.status == status)
    return [serialize(d) for d in query.order_by(Document.upload_date.desc()).all()]

@app.get("/document/{document_id}")
def document(document_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc: raise HTTPException(404, "Document not found")
    return serialize(doc)

@app.put("/document/{document_id}")
def update_document(document_id: int, payload: DocumentUpdate, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc: raise HTTPException(404, "Document not found")
    for change in payload.fields:
        field = db.query(ExtractedField).filter_by(document_id=document_id, field_name=change.field_name).first()
        if field:
            field.edited_by_user = field.field_value != change.field_value
            field.field_value, field.validated = change.field_value, change.validated
    outcome = resolve_review_action(payload.action, payload.reason)
    if outcome["status"] is not None: doc.status = outcome["status"]
    if outcome["review_required"] is not None: doc.review_required = outcome["review_required"]
    log(db, doc.id, outcome["log_action"], outcome["log_details"])
    db.commit(); db.refresh(doc); return serialize(doc)

@app.get("/export/{document_id}")
def export(document_id: int, format: str = "json", db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc: raise HTTPException(404, "Document not found")
    log(db, doc.id, "Exported", format.upper()); db.commit(); data = serialize(doc)
    if format == "json": return data
    if format != "csv": raise HTTPException(400, "format must be csv or json")
    return StreamingResponse(iter([_to_csv(data["fields"])]), media_type="text/csv", headers={"Content-Disposition":f'attachment; filename="document-{doc.id}.csv"'})
