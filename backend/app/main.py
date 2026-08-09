import csv, io, json, logging, shutil
from datetime import datetime, timezone
from pathlib import Path
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session
from .config import CORS_ORIGINS
from . import auth, config
from .security import rate_limit
from .database import Base, engine, get_db
from .jobs import run_pipeline_task, reset_stuck_processing
from .models import AuditLog, Document, ExtractedField, SchemaDefinition, User
from .schemas import DocumentUpdate, LoginRequest, PasswordChange, SchemaPayload, TokenResponse, UserCreate, UserOut
from .services import available_schemas, log, process_document, resolve_review_action, schema_for
from .storage import get_storage

# Fail fast if a production (non-sqlite) deploy is missing a strong JWT_SECRET.
config.check_production_config()

# Tests/dev create the schema directly; prod runs Alembic migrations on deploy.
if config.DATABASE_URL.startswith("sqlite"):
    Base.metadata.create_all(bind=engine)
app = FastAPI(title="Document Intelligence Engine", version="1.0.0")
_log = logging.getLogger("aethermind")

# Catch-all so an unexpected error becomes a real JSON 500 that flows back OUT
# through the CORS middleware below. Registered BEFORE CORSMiddleware so CORS is
# the outer layer — otherwise Starlette's default 500 skips CORS and the browser
# only sees "Failed to fetch" instead of the actual error/status.
@app.middleware("http")
async def _surface_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:
        _log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def _bootstrap():
    from .services import bootstrap_admin
    db = next(get_db())
    try:
        bootstrap_admin(db)
        reset_stuck_processing(db)
    finally:
        db.close()

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
    return {"id":d.id,"filename":d.filename,"document_type":d.document_type,"upload_date":d.upload_date,"status":d.status,"processing_time":d.processing_time,"confidence":d.confidence,"review_required":d.review_required,"pipeline_trace":d.pipeline_trace,"anomalies":d.anomalies,"fields":[{"id":f.id,"field_name":f.field_name,"field_value":f.field_value,"original_value":f.original_value,"confidence":f.confidence,"validated":f.validated,"edited_by_user":f.edited_by_user,"source_quote":f.source_quote,"grounded":f.grounded} for f in d.extracted_fields],"audit":[{"action":a.action,"timestamp":a.timestamp,"details":a.details,"actor_email":a.actor_email} for a in d.audit_logs]}

@app.get("/health")
def health(): return {"status":"ok"}

@app.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(email=payload.email).first()
    if not user or not user.is_active or not auth.verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    return {"access_token": auth.create_access_token(user), "token_type": "bearer",
            "user": {"id": user.id, "email": user.email, "role": user.role, "is_active": user.is_active}}

@app.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(auth.get_current_user)):
    return {"id": user.id, "email": user.email, "role": user.role, "is_active": user.is_active}

@app.post("/auth/change-password")
def change_password(payload: PasswordChange, db: Session = Depends(get_db),
                    user: User = Depends(auth.get_current_user)):
    if not auth.verify_password(payload.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    user.password_hash = auth.hash_password(payload.new_password); db.commit()
    return {"status": "ok"}

@app.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(auth.require_role("admin"))):
    return [{"id": u.id, "email": u.email, "role": u.role, "is_active": u.is_active}
            for u in db.query(User).order_by(User.created_at.desc()).all()]

@app.post("/users", status_code=201, response_model=UserOut)
def create_user_endpoint(payload: UserCreate, db: Session = Depends(get_db),
                         _: User = Depends(auth.require_role("admin"))):
    if db.query(User).filter_by(email=payload.email).first():
        raise HTTPException(409, "Email already exists")
    from .services import create_user
    u = create_user(db, payload.email, payload.password, payload.role)
    return {"id": u.id, "email": u.email, "role": u.role, "is_active": u.is_active}

@app.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, role: str | None = None, is_active: bool | None = None,
                db: Session = Depends(get_db), _: User = Depends(auth.require_role("admin"))):
    if role is not None and role not in {"admin", "reviewer", "viewer"}:
        raise HTTPException(400, "Invalid role")
    u = db.get(User, user_id)
    if not u: raise HTTPException(404, "User not found")
    if role is not None: u.role = role
    if is_active is not None: u.is_active = is_active
    db.commit()
    return {"id": u.id, "email": u.email, "role": u.role, "is_active": u.is_active}

@app.get("/schemas")
def list_schemas(db: Session = Depends(get_db), _: User = Depends(auth.get_current_user)): return available_schemas(db)

@app.post("/schemas", status_code=201)
def create_schema(payload: SchemaPayload, db: Session = Depends(get_db),
                  _: User = Depends(auth.require_role("admin"))):
    if payload.key in [s["key"] for s in available_schemas(db)]: raise HTTPException(409, "Schema key already exists")
    item = SchemaDefinition(**payload.model_dump()); db.add(item); db.commit(); return {"key":item.key,"name":item.name,"fields":item.fields}

@app.post("/upload", status_code=201, dependencies=[Depends(rate_limit)])
async def upload(file: UploadFile = File(...), document_type: str = "invoice", db: Session = Depends(get_db),
                 user: User = Depends(auth.require_role("admin", "reviewer"))):
    if Path(file.filename or "").suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg"}: raise HTTPException(400, "Only PDF, PNG, and JPEG are supported")
    try: schema_for(db, document_type)
    except ValueError as exc: raise HTTPException(400, str(exc))
    data = await file.read()
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024: raise HTTPException(413, f"File exceeds the {config.MAX_UPLOAD_MB} MB limit")
    safe_name = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{Path(file.filename).name}"
    try:
        key = get_storage().save(safe_name, data)
    except Exception as exc:
        _log.exception("Storage upload failed")
        raise HTTPException(502, f"Storage upload failed: {exc}")
    doc = Document(filename=file.filename or safe_name, document_type=document_type, stored_path=key)
    db.add(doc); db.flush(); log(db, doc.id, "Uploaded", f"Schema selected: {document_type}", actor=user); db.commit(); db.refresh(doc)
    return serialize(doc)

@app.post("/process/{document_id}", status_code=202, dependencies=[Depends(rate_limit)])
def process(document_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db),
            user: User = Depends(auth.require_role("admin", "reviewer"))):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    if doc.status == "processing":
        raise HTTPException(409, "Document is already processing")
    doc.status = "processing"; db.commit(); db.refresh(doc)
    background_tasks.add_task(run_pipeline_task, doc.id, user.id)
    return serialize(doc)

@app.get("/documents")
def documents(q: str = "", status: str = "", db: Session = Depends(get_db), _: User = Depends(auth.get_current_user)):
    query = db.query(Document)
    if q: query = query.filter(Document.filename.ilike(f"%{q}%"))
    if status: query = query.filter(Document.status == status)
    return [serialize(d) for d in query.order_by(Document.upload_date.desc()).all()]

@app.get("/document/{document_id}")
def document(document_id: int, db: Session = Depends(get_db), _: User = Depends(auth.get_current_user)):
    doc = db.get(Document, document_id)
    if not doc: raise HTTPException(404, "Document not found")
    return serialize(doc)

@app.put("/document/{document_id}")
def update_document(document_id: int, payload: DocumentUpdate, db: Session = Depends(get_db),
                    user: User = Depends(auth.require_role("admin", "reviewer"))):
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
    log(db, doc.id, outcome["log_action"], outcome["log_details"], actor=user)
    db.commit(); db.refresh(doc); return serialize(doc)

@app.get("/export/{document_id}")
def export(document_id: int, format: str = "json", db: Session = Depends(get_db), user: User = Depends(auth.get_current_user)):
    doc = db.get(Document, document_id)
    if not doc: raise HTTPException(404, "Document not found")
    log(db, doc.id, "Exported", format.upper(), actor=user); db.commit(); data = serialize(doc)
    if format == "json": return data
    if format != "csv": raise HTTPException(400, "format must be csv or json")
    return StreamingResponse(iter([_to_csv(data["fields"])]), media_type="text/csv", headers={"Content-Disposition":f'attachment; filename="document-{doc.id}.csv"'})
