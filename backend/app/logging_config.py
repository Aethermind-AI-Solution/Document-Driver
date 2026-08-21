import json
import logging
from datetime import datetime, timezone
from . import config

_EXTRA_KEYS = ("document_id", "stage", "latency_ms", "actor", "status")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in _EXTRA_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Install a single stdlib handler on the root logger, JSON or plain per
    config.LOG_FORMAT. Idempotent (removes any handler we previously added);
    never raises at startup."""
    try:
        root = logging.getLogger()
        for handler in [h for h in root.handlers if getattr(h, "_aethermind", False)]:
            root.removeHandler(handler)
        handler = logging.StreamHandler()
        handler._aethermind = True
        if config.LOG_FORMAT == "json":
            handler.setFormatter(JsonFormatter())
        else:
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        logging.getLogger("aethermind").setLevel(logging.INFO)
    except Exception:
        pass
