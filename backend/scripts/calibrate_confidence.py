"""Run extraction over the golden set and report confidence separation.
Runs in fallback mode ($0) by default; set OPENAI_API_KEY for the AI path."""
import sys
from app import services, goldens, calibration

def value_matches(true_v: str, got_v: str | None) -> bool:
    from app.services import _norm
    return got_v is not None and _norm(str(true_v)) == _norm(str(got_v))

def main(golden_path: str, invoices_dir: str) -> None:
    items = goldens.load_golden_set(golden_path)
    records = []
    for item in items:
        schema = {"fields": [{"name": k} for k in item["fields"]]}
        text = services.extract_text(f"{invoices_dir}/{item['doc']}")
        data = services.fallback_extract(text, schema["fields"])
        grounded = services._ground_fields(data, schema["fields"], text)
        by_name = {g["field_name"]: g for g in grounded}
        for fname, true_v in item["fields"].items():
            g = by_name.get(fname, {})
            records.append({"confidence": g.get("confidence", 0.0),
                            "correct": value_matches(true_v, g.get("field_value"))})
    print(calibration.separation(records))

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
