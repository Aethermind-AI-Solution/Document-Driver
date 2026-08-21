import csv, io, json, logging, shutil
from datetime import datetime, timezone
from pathlib import Path
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session
from .config import CORS_ORIGINS
from . import auth, config, mcp_server
from .logging_config import configure_logging
from .security import rate_limit
from .database import Base, engine, get_db
from .jobs import run_pipeline_task, reset_stuck_processing
from .models import AuditLog, AutoApproveConfig, Document, ExtractedField, SchemaDefinition, User, WebhookConfig
from .schemas import AutoApproveCreate, AutoApproveOut, AutoApproveUpdate, DocumentUpdate, LoginRequest, PasswordChange, SchemaEditPayload, SchemaPayload, SuggestedSchemaOut, TokenResponse, UserCreate, UserOut, WebhookCreate, WebhookUpdate, WebhookOut
from .services import all_schema_keys, apply_approval, available_schemas, can_transition, log, resolve_review_action, schema_for
from .storage import get_storage
from .webhooks import deliver_webhook
from . import eval as eval_mod

# Fail fast if a production (non-sqlite) deploy is missing a strong JWT_SECRET.
config.check_production_config()

# Tests/dev create the schema directly; prod runs Alembic migrations on deploy.
if config.DATABASE_URL.startswith("sqlite"):
    Base.metadata.create_all(bind=engine)

configure_logging()
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

def serialize_summary(d: Document):
    return {"id": d.id, "filename": d.filename, "document_type": d.document_type,
            "upload_date": d.upload_date, "status": d.status, "confidence": d.confidence,
            "review_required": d.review_required, "processing_time": d.processing_time,
            "anomalies": d.anomalies, "auto_approved": d.auto_approved}

@app.get("/health")
def health(): return {"status":"ok"}

@app.post("/auth/login", response_model=TokenResponse, dependencies=[Depends(rate_limit)])
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
    if payload.key in all_schema_keys(db): raise HTTPException(409, "Schema key already exists")
    item = SchemaDefinition(**payload.model_dump()); db.add(item); db.commit(); return {"key":item.key,"name":item.name,"fields":item.fields}

@app.get("/schemas/suggested", response_model=list[SuggestedSchemaOut])
def list_suggested_schemas(db: Session = Depends(get_db),
                           _: User = Depends(auth.require_role("admin"))):
    rows = (db.query(SchemaDefinition).filter_by(status="suggested")
            .order_by(SchemaDefinition.created_at.desc(), SchemaDefinition.id.desc()).all())
    return [{"id": s.id, "key": s.key, "name": s.name, "fields": s.fields,
             "origin_document_id": s.origin_document_id, "created_at": s.created_at} for s in rows]

@app.patch("/schemas/{schema_id}")
def edit_schema(schema_id: int, payload: SchemaEditPayload, db: Session = Depends(get_db),
                user: User = Depends(auth.require_role("admin"))):
    s = db.get(SchemaDefinition, schema_id)
    if not s: raise HTTPException(404, "Schema not found")
    if payload.name is not None: s.name = payload.name
    if payload.fields is not None: s.fields = [f.model_dump() for f in payload.fields]
    if s.origin_document_id: log(db, s.origin_document_id, "Schema edited", s.key, actor=user)
    db.commit(); db.refresh(s)
    return {"id": s.id, "key": s.key, "name": s.name, "fields": s.fields, "status": s.status}

@app.post("/schemas/{schema_id}/approve")
def approve_schema(schema_id: int, db: Session = Depends(get_db),
                   user: User = Depends(auth.require_role("admin"))):
    s = db.get(SchemaDefinition, schema_id)
    if not s: raise HTTPException(404, "Schema not found")
    s.status = "approved"
    if s.origin_document_id: log(db, s.origin_document_id, "Schema approved", s.key, actor=user)
    db.commit(); db.refresh(s)
    return {"id": s.id, "key": s.key, "name": s.name, "status": s.status}

@app.delete("/schemas/{schema_id}", status_code=204)
def reject_schema(schema_id: int, db: Session = Depends(get_db),
                  user: User = Depends(auth.require_role("admin"))):
    s = db.get(SchemaDefinition, schema_id)
    if not s: raise HTTPException(404, "Schema not found")
    if s.origin_document_id: log(db, s.origin_document_id, "Schema rejected", s.key, actor=user)
    db.delete(s); db.commit()

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
    if doc.status not in {"uploaded", "error", "review_required", "processed"}:
        raise HTTPException(409, f"Cannot reprocess a document in state '{doc.status}'")
    doc.status = "processing"; db.commit(); db.refresh(doc)
    background_tasks.add_task(run_pipeline_task, doc.id, user.id)
    return serialize(doc)

@app.get("/documents")
def documents(q: str = "", status: str = "", limit: int = 20, offset: int = 0,
              db: Session = Depends(get_db), _: User = Depends(auth.get_current_user)):
    limit = max(1, min(limit, 100)); offset = max(0, offset)
    query = db.query(Document)
    if q: query = query.filter(Document.filename.ilike(f"%{q}%"))
    if status: query = query.filter(Document.status == status)
    total = query.count()
    items = query.order_by(Document.upload_date.desc()).offset(offset).limit(limit).all()
    return {"items": [serialize_summary(d) for d in items], "total": total}


@app.get("/documents/stats")
def documents_stats(db: Session = Depends(get_db), _: User = Depends(auth.get_current_user)):
    total = db.query(Document).count()
    review_required = db.query(Document).filter(Document.review_required.is_(True)).count()
    times = [t for (t,) in db.query(Document.processing_time)
             .filter(Document.processing_time.isnot(None)).all()]
    avg = round(sum(times) / len(times), 2) if times else None
    approved_fields = (db.query(ExtractedField.edited_by_user)
                       .join(Document, ExtractedField.document_id == Document.id)
                       .filter(Document.status == "approved").all())
    agreement = round(sum(1 for (e,) in approved_fields if not e) / len(approved_fields), 2) \
        if approved_fields else None
    auto = db.query(Document).filter(Document.auto_approved.is_(True)).count()
    auto_reopened = db.query(Document).filter(Document.auto_approved.is_(True),
                                              Document.status == "reopened").count()
    webhook_failed = db.query(Document).filter(Document.webhook_status == "failed").count()
    return {"total": total, "review_required": review_required, "avg_processing_time": avg,
            "field_agreement_rate": agreement, "auto_approved": auto,
            "auto_approved_reopen_rate": round(auto_reopened / auto, 2) if auto else None,
            "webhook_failed": webhook_failed}

@app.get("/document/{document_id}")
def document(document_id: int, db: Session = Depends(get_db), _: User = Depends(auth.get_current_user)):
    doc = db.get(Document, document_id)
    if not doc: raise HTTPException(404, "Document not found")
    return serialize(doc)

@app.put("/document/{document_id}")
def update_document(document_id: int, payload: DocumentUpdate, background_tasks: BackgroundTasks,
                    db: Session = Depends(get_db),
                    user: User = Depends(auth.require_role("admin", "reviewer"))):
    doc = db.get(Document, document_id)
    if not doc: raise HTTPException(404, "Document not found")
    prior_status = doc.status
    # Reopen: finalized -> back into review, fields untouched.
    if payload.action == "reopen":
        if prior_status not in ("approved", "rejected"):
            raise HTTPException(409, f"Cannot reopen a document in state '{prior_status}'")
        if prior_status == "approved":
            if user.role != "admin":
                raise HTTPException(403, "Only an admin can reopen an approved document")
            if not (payload.reason or "").strip():
                raise HTTPException(422, "A reason is required to reopen an approved document")
        doc.status = "reopened"; doc.review_required = True
        reason = (payload.reason or "").strip()
        log(db, doc.id, "Reopened", f"from {prior_status}" + (f": {reason}" if reason else ""), actor=user)
        db.commit(); db.refresh(doc)
        return serialize(doc)
    # Non-reopen actions are not allowed on a finalized document — reopen first.
    if prior_status in ("approved", "rejected"):
        raise HTTPException(409, f"Reopen the document before editing it (state '{prior_status}')")
    for change in payload.fields:
        field = db.query(ExtractedField).filter_by(document_id=document_id, field_name=change.field_name).first()
        if field:
            field.edited_by_user = field.field_value != change.field_value
            field.field_value, field.validated = change.field_value, change.validated
    outcome = resolve_review_action(payload.action, payload.reason)
    if outcome["status"] == "approved":
        try:
            apply_approval(db, doc, prior_status, actor=user,
                           action_label=outcome["log_action"], details=outcome["log_details"])
        except ValueError as e:
            raise HTTPException(409, str(e))
    else:
        if outcome["status"] is not None:
            if not can_transition(prior_status, outcome["status"]):
                raise HTTPException(409, f"Cannot move a document from '{prior_status}' to '{outcome['status']}'")
            doc.status = outcome["status"]
        if outcome["review_required"] is not None:
            doc.review_required = outcome["review_required"]
        log(db, doc.id, outcome["log_action"], outcome["log_details"], actor=user)
    db.commit(); db.refresh(doc)
    if outcome["status"] == "approved":
        cfg = db.query(WebhookConfig).filter_by(document_type=doc.document_type, active=True).first()
        if cfg:
            doc.webhook_status = "pending"; db.commit()
            background_tasks.add_task(deliver_webhook, cfg.id, doc.id, user.email)
    return serialize(doc)

@app.get("/export/{document_id}")
def export(document_id: int, format: str = "json", db: Session = Depends(get_db), user: User = Depends(auth.get_current_user)):
    doc = db.get(Document, document_id)
    if not doc: raise HTTPException(404, "Document not found")
    log(db, doc.id, "Exported", format.upper(), actor=user); db.commit(); data = serialize(doc)
    if format == "json": return data
    if format != "csv": raise HTTPException(400, "format must be csv or json")
    return StreamingResponse(iter([_to_csv(data["fields"])]), media_type="text/csv", headers={"Content-Disposition":f'attachment; filename="document-{doc.id}.csv"'})

def _webhook_out(w: WebhookConfig) -> dict:
    return {"id": w.id, "document_type": w.document_type, "url": w.url,
            "active": w.active, "has_secret": bool(w.secret)}

@app.get("/webhooks", response_model=list[WebhookOut])
def list_webhooks(db: Session = Depends(get_db), _: User = Depends(auth.require_role("admin"))):
    return [_webhook_out(w) for w in db.query(WebhookConfig).order_by(WebhookConfig.document_type).all()]

@app.post("/webhooks", status_code=201, response_model=WebhookOut)
def create_webhook(payload: WebhookCreate, db: Session = Depends(get_db),
                   _: User = Depends(auth.require_role("admin"))):
    if db.query(WebhookConfig).filter_by(document_type=payload.document_type).first():
        raise HTTPException(409, "A webhook for that document type already exists")
    w = WebhookConfig(**payload.model_dump()); db.add(w); db.commit(); db.refresh(w)
    return _webhook_out(w)

@app.patch("/webhooks/{webhook_id}", response_model=WebhookOut)
def update_webhook(webhook_id: int, payload: WebhookUpdate, db: Session = Depends(get_db),
                   _: User = Depends(auth.require_role("admin"))):
    w = db.get(WebhookConfig, webhook_id)
    if not w: raise HTTPException(404, "Webhook not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(w, k, v)
    db.commit(); db.refresh(w)
    return _webhook_out(w)

@app.delete("/webhooks/{webhook_id}", status_code=204)
def delete_webhook(webhook_id: int, db: Session = Depends(get_db),
                   _: User = Depends(auth.require_role("admin"))):
    w = db.get(WebhookConfig, webhook_id)
    if not w: raise HTTPException(404, "Webhook not found")
    db.delete(w); db.commit()

def _autoapprove_out(c: AutoApproveConfig) -> dict:
    return {"id": c.id, "document_type": c.document_type, "enabled": c.enabled,
            "min_confidence": c.min_confidence, "created_at": c.created_at}

@app.get("/auto-approve", response_model=list[AutoApproveOut])
def list_auto_approve(db: Session = Depends(get_db), _: User = Depends(auth.require_role("admin"))):
    return [_autoapprove_out(c) for c in db.query(AutoApproveConfig).order_by(AutoApproveConfig.document_type).all()]

@app.post("/auto-approve", status_code=201, response_model=AutoApproveOut)
def create_auto_approve(payload: AutoApproveCreate, db: Session = Depends(get_db),
                        _: User = Depends(auth.require_role("admin"))):
    if db.query(AutoApproveConfig).filter_by(document_type=payload.document_type).first():
        raise HTTPException(409, "An auto-approve config for that document type already exists")
    c = AutoApproveConfig(**payload.model_dump()); db.add(c); db.commit(); db.refresh(c)
    return _autoapprove_out(c)

@app.patch("/auto-approve/{config_id}", response_model=AutoApproveOut)
def update_auto_approve(config_id: int, payload: AutoApproveUpdate, db: Session = Depends(get_db),
                        user: User = Depends(auth.require_role("admin"))):
    c = db.get(AutoApproveConfig, config_id)
    if not c: raise HTTPException(404, "Auto-approve config not found")
    was_enabled = c.enabled
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(c, k, v)
    if not was_enabled and c.enabled:
        _log.info("Auto-approve ENABLED for %s (floor %.2f) by %s",
                  c.document_type, c.min_confidence, user.email)
    db.commit(); db.refresh(c)
    return _autoapprove_out(c)

@app.delete("/auto-approve/{config_id}", status_code=204)
def delete_auto_approve(config_id: int, db: Session = Depends(get_db),
                        _: User = Depends(auth.require_role("admin"))):
    c = db.get(AutoApproveConfig, config_id)
    if not c: raise HTTPException(404, "Auto-approve config not found")
    db.delete(c); db.commit()

@app.get("/auto-approve/eval/{document_type}")
def auto_approve_eval(document_type: str, db: Session = Depends(get_db),
                      _: User = Depends(auth.require_role("admin"))):
    return eval_mod.build_report(eval_mod.correction_records(db, document_type))

def mount_mcp(app) -> bool:
    """Mount the MCP server at /mcp only when a token is configured."""
    if not config.MCP_API_TOKEN:
        return False
    mcp_app = mcp_server.build_mcp().streamable_http_app()
    app.mount("/mcp", mcp_server.TokenAuthASGI(mcp_app, config.MCP_API_TOKEN))
    # FastMCP's streamable HTTP session manager runs via the sub-app's lifespan
    # (it starts/stops a StreamableHTTPSessionManager); without wiring it into
    # the parent app's lifespan, requests that reach the sub-app 500 because the
    # session manager was never started. Verified by direct test: mounting
    # without this produced a 500 on an authenticated /mcp/ request, while
    # running mcp_app standalone (its own lifespan triggered) returned 200.
    prev = app.router.lifespan_context
    import contextlib
    @contextlib.asynccontextmanager
    async def _combined(a):
        async with mcp_app.router.lifespan_context(mcp_app):
            async with prev(a):
                yield
    app.router.lifespan_context = _combined
    return True

mount_mcp(app)
