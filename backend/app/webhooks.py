import hmac
import json
import logging
from datetime import datetime, timezone
from hashlib import sha256
import requests
from .database import SessionLocal
from .models import Document, WebhookConfig
from .services import log

_log = logging.getLogger("aethermind")


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


def deliver_webhook(config_id: int, document_id: int, approved_by: str) -> None:
    db = SessionLocal()
    try:
        cfg = db.get(WebhookConfig, config_id)
        doc = db.get(Document, document_id)
        if not cfg or not cfg.active or not doc:
            return
        body = json.dumps(build_payload(doc, approved_by)).encode()
        headers = {"Content-Type": "application/json"}
        if cfg.secret:
            headers["X-Aethermind-Signature"] = sign(body, cfg.secret)
        try:
            resp = requests.post(cfg.url, data=body, headers=headers, timeout=10)
            if 200 <= resp.status_code < 300:
                log(db, doc.id, "Webhook delivered", f"{cfg.url} ({resp.status_code})")
            else:
                log(db, doc.id, "Webhook failed", f"{cfg.url} -> HTTP {resp.status_code}")
        except Exception as exc:
            log(db, doc.id, "Webhook failed", f"{cfg.url} -> {type(exc).__name__}: {exc}")
        db.commit()
    except Exception:
        _log.exception("deliver_webhook error for config %s / doc %s", config_id, document_id)
    finally:
        db.close()
