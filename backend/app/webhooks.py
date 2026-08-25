import hmac
import json
import logging
import time
from datetime import datetime, timezone
from hashlib import sha256
import requests
from .database import SessionLocal
from .models import Document, WebhookConfig
from .services import log

_log = logging.getLogger("aethermind")

_RETRIES = 3
_RETRY_BACKOFF = 0.2


def build_payload(doc: Document, approved_by: str) -> dict:
    return {
        "event": "document.approved", "document_id": doc.id, "filename": doc.filename,
        "document_type": doc.document_type, "status": doc.status, "confidence": doc.confidence,
        "revision": doc.revision,
        "approved_by": approved_by,
        "fields": [{"field_name": f.field_name, "field_value": f.field_value,
                    "confidence": f.confidence, "grounded": f.grounded}
                   for f in doc.extracted_fields],
        "anomalies": doc.anomalies, "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, sha256).hexdigest()


def deliver_webhook(config_id: int, document_id: int, approved_by: str, org_id: int | None = None) -> None:
    db = SessionLocal()
    from .context import set_current_org
    set_current_org(org_id)
    try:
        cfg = db.get(WebhookConfig, config_id)
        doc = db.get(Document, document_id)
        if not cfg or not cfg.active or not doc:
            return
        body = json.dumps(build_payload(doc, approved_by)).encode()
        headers = {"Content-Type": "application/json"}
        if cfg.secret:
            headers["X-Aethermind-Signature"] = sign(body, cfg.secret)
        delivered = False
        reason = None
        for attempt in range(1, _RETRIES + 1):
            try:
                resp = requests.post(cfg.url, data=body, headers=headers, timeout=10)
                if 200 <= resp.status_code < 300:
                    doc.webhook_status = "delivered"
                    doc.webhook_detail = f"{cfg.url} ({resp.status_code})"
                    log(db, doc.id, "Webhook delivered", doc.webhook_detail)
                    delivered = True
                    break
                reason = f"{cfg.url} -> HTTP {resp.status_code}"
            except Exception as exc:
                reason = f"{cfg.url} -> {type(exc).__name__}: {exc}"
            if attempt < _RETRIES:
                time.sleep(_RETRY_BACKOFF * attempt)
        if not delivered:
            doc.webhook_status = "failed"
            doc.webhook_detail = reason
            log(db, doc.id, "Webhook failed", reason)
        db.commit()
    except Exception:
        _log.exception("deliver_webhook error for config %s / doc %s", config_id, document_id)
    finally:
        db.close()
