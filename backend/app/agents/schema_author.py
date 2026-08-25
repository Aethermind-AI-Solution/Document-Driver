import json
import re
from .. import config, services
from ..models import SchemaDefinition

_ALLOWED_TYPES = {"string", "number", "date", "array"}
_MAX_FIELDS = 25

_RESPONSE_FORMAT = {
    "format": {
        "type": "json_schema", "name": "schema_proposal", "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "name": {"type": "string"},
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "label": {"type": "string"},
                            "type": {"type": "string"},
                            "required": {"type": "boolean"},
                        },
                        "required": ["name", "label", "type", "required"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["key", "name", "fields"],
            "additionalProperties": False,
        },
    }
}


def _client():
    from openai import AsyncOpenAI
    return AsyncOpenAI()


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", (s or "").strip().lower()).strip("-_")


def _field_name(s: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", (s or "").strip().lower()).strip("_")


def _uniquify(key: str, existing: set[str]) -> str:
    if key not in existing:
        return key
    i = 2
    while f"{key}-{i}" in existing:
        i += 1
    return f"{key}-{i}"


def _normalize(data: dict, existing_keys: set[str]) -> dict | None:
    key = _slug(str(data.get("key") or data.get("name") or ""))
    name = str(data.get("name") or "").strip() or (key.replace("-", " ").replace("_", " ").title() if key else "")
    fields, seen = [], set()
    for f in (data.get("fields") or []):
        if not isinstance(f, dict):
            continue
        fname = _field_name(str(f.get("name") or ""))
        if not fname or fname in seen:
            continue
        seen.add(fname)
        ftype = f.get("type") if f.get("type") in _ALLOWED_TYPES else "string"
        label = str(f.get("label") or "").strip() or fname.replace("_", " ").title()
        fields.append({"name": fname, "label": label, "type": ftype,
                       "required": bool(f.get("required", False))})
    if not key or not name or not (1 <= len(fields) <= _MAX_FIELDS):
        return None
    return {"key": _uniquify(key, existing_keys), "name": name, "fields": fields}


async def propose_schema(text: str, existing_keys: set[str]) -> dict | None:
    """Ask the model to propose a schema for an unrecognized document.
    Returns a normalized {key, name, fields} dict, or None on any failure."""
    try:
        client = _client()
        prompt = ("This document did not match any known type. Propose a concise schema to "
                  "extract its key fields. Return JSON {\"key\":\"snake-or-kebab-slug\","
                  "\"name\":\"Human Name\",\"fields\":[{\"name\":\"snake_case\",\"label\":"
                  "\"Human\",\"type\":\"string|number|date|array\",\"required\":bool}]}. "
                  "Use at most 25 fields.\n\nDocument:\n" + (text or "")[:4000])
        resp = await client.responses.create(
            model=config.CLASSIFIER_MODEL,
            input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            text=_RESPONSE_FORMAT)
        return _normalize(json.loads(resp.output_text), existing_keys)
    except Exception:
        return None


def persist_suggested(db, proposal: dict, origin_document_id: int, actor=None, org_id=None) -> SchemaDefinition:
    row = SchemaDefinition(key=proposal["key"], name=proposal["name"], fields=proposal["fields"],
                           status="suggested", origin_document_id=origin_document_id, org_id=org_id)
    db.add(row)
    db.flush()
    services.log(db, origin_document_id, "Schema suggested", f'{row.key}: {row.name}', actor=actor)
    return row
