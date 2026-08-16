from dataclasses import dataclass
from math import sqrt

from . import services
from .models import Document, ExtractedField

# Bucket edges aligned to the fixed _ground confidence constants.
_BUCKETS = [(0.0, 0.475, "0.40"), (0.475, 0.625, "0.55"), (0.625, 0.80, "0.70"),
            (0.80, 0.925, "0.90"), (0.925, 1.01, "0.95")]
_BUCKET_LABELS = ["0.40", "0.55", "0.70", "0.90", "0.95"]


@dataclass
class EvalRecord:
    document_type: str
    field_name: str
    required: bool
    confidence: float
    grounded: str
    correct: bool
    source: str            # "corrections" | "golden"


def wilson_lower_bound(successes: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    phat = successes / n
    denom = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def _bucket(conf: float) -> str:
    for lo, hi, label in _BUCKETS:
        if lo <= conf < hi:
            return label
    return "0.95" if conf >= 0.925 else "0.40"


def per_field_accuracy(records, min_n: int = 30) -> list[dict]:
    groups: dict = {}
    for r in records:
        groups.setdefault((r.document_type, r.field_name), []).append(r)
    out = []
    for (dt, fn), rs in groups.items():
        n = len(rs)
        correct = sum(1 for r in rs if r.correct)
        out.append({"document_type": dt, "field_name": fn, "required": rs[0].required,
                    "n": n, "correct_rate": correct / n if n else 0.0,
                    "wilson_low": wilson_lower_bound(correct, n), "enough": n >= min_n})
    return sorted(out, key=lambda d: (d["document_type"], d["field_name"]))


def reliability_table(records, min_n: int = 30) -> list[dict]:
    buckets: dict = {}
    for r in records:
        buckets.setdefault(_bucket(r.confidence), []).append(r)
    out = []
    for label in _BUCKET_LABELS:
        rs = buckets.get(label, [])
        n = len(rs)
        correct = sum(1 for r in rs if r.correct)
        out.append({"bucket": label, "n": n,
                    "observed_correct_rate": (correct / n) if n else None,
                    "wilson_low": wilson_lower_bound(correct, n), "enough": n >= min_n})
    return out


def grounded_but_wrong_rate(records) -> dict:
    hi = [r for r in records if r.confidence >= 0.9]
    n = len(hi)
    wrong = sum(1 for r in hi if not r.correct)
    return {"n_high_conf": n, "n_wrong": wrong, "rate": (wrong / n) if n else None}


def build_report(records, min_n: int = 30) -> dict:
    return {
        "n": len(records),
        "header": (f"Correction-derived agreement — unaudited-but-approved fields are counted "
                   f"correct; this OVERSTATES accuracy (upper bound). N={len(records)}."),
        "per_field": per_field_accuracy(records, min_n),
        "reliability": reliability_table(records, min_n),
        "grounded_but_wrong": grounded_but_wrong_rate(records),
    }


def correction_records(db, document_type: str | None = None) -> list[EvalRecord]:
    """EvalRecords from APPROVED documents' fields. correct = the AI value was NOT
    later changed by a human (unedited fields are presumed — weakly — correct)."""
    q = (db.query(ExtractedField, Document.document_type)
         .join(Document, ExtractedField.document_id == Document.id)
         .filter(Document.status == "approved"))
    if document_type:
        q = q.filter(Document.document_type == document_type)
    schema_cache: dict = {}
    out = []
    for ef, dt in q.all():
        if dt not in schema_cache:
            try:
                schema_cache[dt] = {f["name"]: f for f in services.schema_for(db, dt)["fields"]}
            except Exception:
                schema_cache[dt] = {}
        required = bool(schema_cache[dt].get(ef.field_name, {}).get("required"))
        correct = not (ef.edited_by_user and ef.original_value is not None
                       and ef.field_value is not None and ef.original_value != ef.field_value)
        out.append(EvalRecord(dt, ef.field_name, required, ef.confidence,
                              ef.grounded or "unverified", correct, "corrections"))
    return out


def golden_records(db, fixture: dict) -> list[EvalRecord]:
    """EvalRecords comparing stored extraction against a hand-labeled golden fixture:
    {"documents": [{"document_id": int, "expected": {field_name: value}}]}."""
    out = []
    for entry in fixture.get("documents", []):
        doc = db.get(Document, entry["document_id"])
        if not doc:
            continue
        try:
            schema = {f["name"]: f for f in services.schema_for(db, doc.document_type)["fields"]}
        except Exception:
            schema = {}
        by_name = {ef.field_name: ef for ef in doc.extracted_fields}
        for fname, expected in (entry.get("expected") or {}).items():
            ef = by_name.get(fname)
            if ef is None:
                continue
            required = bool(schema.get(fname, {}).get("required"))
            correct = services._norm(str(ef.field_value)) == services._norm(str(expected)) \
                if ef.field_value is not None else (expected in (None, ""))
            out.append(EvalRecord(doc.document_type, fname, required, ef.confidence,
                                  ef.grounded or "unverified", bool(correct), "golden"))
    return out
