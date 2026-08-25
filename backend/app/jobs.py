import asyncio
import logging
from sqlalchemy.orm import Session
from .database import SessionLocal
from .models import Document, User
from .services import log
from .agents.pipeline import run_pipeline

_log = logging.getLogger("aethermind")


def run_pipeline_task(document_id: int, actor_id: int | None = None, org_id: int | None = None) -> None:
    """Run the pipeline in a fresh DB session after the HTTP response is sent.
    run_pipeline sets status='error' + logs on failure; the guard here just keeps
    the background worker alive."""
    db = SessionLocal()
    from .context import set_current_org
    set_current_org(org_id)
    try:
        doc = db.get(Document, document_id)
        if not doc:
            return
        actor = db.get(User, actor_id) if actor_id else None
        asyncio.run(run_pipeline(db, doc, doc.document_type, actor=actor))
    except Exception:
        _log.exception("Background pipeline failed for document %s", document_id)
    finally:
        db.close()


def reset_stuck_processing(db: Session) -> int:
    """Docs stranded in 'processing' (instance restarted mid-run) → 'error', retryable."""
    stuck = db.query(Document).execution_options(skip_org_filter=True).filter(Document.status == "processing").all()
    for doc in stuck:
        doc.status = "error"
        log(db, doc.id, "Processing failed", "Processing interrupted (server restart)")
    db.commit()
    return len(stuck)
