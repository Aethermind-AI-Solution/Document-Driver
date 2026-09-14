"""Does grounding-derived confidence separate correct from wrong extractions?
Input records are {"confidence": float, "correct": bool}; correctness is decided
by the caller against the golden set."""

def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 3) if xs else None

def separation(records: list[dict]) -> dict:
    correct = [r["confidence"] for r in records if r["correct"]]
    wrong = [r["confidence"] for r in records if not r["correct"]]
    mc, mw = _mean(correct), _mean(wrong)
    margin = round(mc - mw, 3) if (mc is not None and mw is not None) else None
    return {"n": len(records), "mean_correct": mc, "mean_wrong": mw, "margin": margin}
