import time
import logging
from pathlib import Path
from dataclasses import asdict
from sqlalchemy.orm import Session
from .. import services, storage
from ..models import Document, ExtractedField, WebhookConfig
from ..webhooks import deliver_webhook
from .base import PipelineContext
from .pages import split_pages
from .classifier import ClassifierAgent
from .extractor import ExtractorAgent
from .reconciler import ReconcilerAgent
from .validator import ValidatorAgent

_log = logging.getLogger("aethermind")

STAGES = [ClassifierAgent, ExtractorAgent, ReconcilerAgent, ValidatorAgent]


def detect_duplicate(db: Session, document: Document) -> list[dict]:
    """Return a duplicate anomaly (as a one-item list) if another doc in the org
    shares this fingerprint, else an empty list."""
    dup = services.find_duplicate(db, document)
    if dup is None:
        return []
    return [{"type": "duplicate_invoice",
             "message": f"Possible duplicate of {dup.filename}",
             "duplicate_of": dup.id}]


def _maybe_auto_approve(db: Session, document: Document) -> None:
    if not services.should_auto_approve(db, document):
        return
    services.apply_approval(db, document, prior_status=document.status, actor=None,
                            action_label="Auto-approved",
                            details=f"confidence {document.confidence:.2f} >= floor; validator-clean, no anomalies")
    _log.info("document auto-approved", extra={"document_id": document.id,
                                               "actor": "system:auto-approve"})
    document.auto_approved = True
    cfg_w = db.query(WebhookConfig).filter_by(document_type=document.document_type, active=True).first()
    if cfg_w:
        document.webhook_status = "pending"
    db.commit(); db.refresh(document)
    if cfg_w:
        deliver_webhook(cfg_w.id, document.id, "system:auto-approve", document.org_id)


async def run_pipeline(db: Session, document: Document, hint_type: str, actor=None) -> Document:
    started = time.perf_counter()
    document.status = "processing"; db.commit()
    try:
        data = storage.get_storage().open(document.stored_path)
        suffix = Path(document.stored_path).suffix
        ctx = PipelineContext(db=db, document=document, hint_type=hint_type, actor=actor)
        ctx.pages = split_pages(data, suffix)
        for AgentCls in STAGES:
            await AgentCls().run(ctx)

        errored = [s.name for s in ctx.trace if s.status == "error"]
        if errored:
            raise RuntimeError(f"Pipeline stage(s) failed: {', '.join(errored)}")

        db.query(ExtractedField).filter_by(document_id=document.id).delete()
        for f in ctx.fields:
            db.add(ExtractedField(document_id=document.id, original_value=f["field_value"],
                                  org_id=document.org_id, **f))
        document.fingerprint = services.compute_fingerprint(ctx.fields)
        dup_anomalies = detect_duplicate(db, document)
        document.pipeline_trace = [asdict(s) for s in ctx.trace]
        document.anomalies = (ctx.anomalies or []) + dup_anomalies or None
        document.confidence = services.document_confidence(ctx.fields, ctx.schema["fields"])
        document.review_required = bool(ctx.anomalies) or any(
            f["confidence"] < .9 or not f["validated"] for f in ctx.fields)
        document.status = "review_required" if document.review_required else "processed"
        document.processing_time = round(time.perf_counter() - started, 2)
        services.log(db, document.id, "Processed",
                     f"Applied {document.document_type} schema", actor=actor)
        db.commit(); db.refresh(document)
        _maybe_auto_approve(db, document)
        _log.info("pipeline complete", extra={"document_id": document.id, "stage": "pipeline",
                                              "latency_ms": int((document.processing_time or 0) * 1000),
                                              "status": document.status})
        return document
    except Exception as exc:
        document.status = "error"
        services.log(db, document.id, "Processing failed", str(exc), actor=actor)
        _log.error("pipeline failed", extra={"document_id": document.id, "stage": "pipeline"}, exc_info=True)
        db.commit(); raise
