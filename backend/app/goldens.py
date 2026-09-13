import json
from pathlib import Path

def load_golden_set(path: str) -> list[dict]:
    """Load per-field ground-truth labels for calibration/eval."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, list):
        raise ValueError("golden set must be a JSON array")
    for item in data:
        if not {"doc", "document_type", "fields"} <= set(item):
            raise ValueError(f"golden item missing keys: {item}")
    return data
