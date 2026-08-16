from dataclasses import dataclass
from math import sqrt

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
