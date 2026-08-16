"""Offline extraction-accuracy report. Run from backend/: python scripts/eval.py [--json out.json]

The numbers are CORRECTION-DERIVED (unaudited-but-approved fields count as correct) and
therefore OVERSTATE accuracy — treat as an upper bound. Curate a golden set (--golden) for an
unbiased anchor before relying on these for auto-approve (B9)."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `app` importable

from app.database import SessionLocal
from app import eval as evalmod


def run(db, document_type=None, min_n=30, golden_path=None) -> dict:
    records = evalmod.correction_records(db, document_type)
    if golden_path:
        with open(golden_path) as fh:
            records += evalmod.golden_records(db, json.load(fh))
    return evalmod.build_report(records, min_n)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Extraction-accuracy report (correction-derived; upper bound).")
    ap.add_argument("--document-type")
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--json", dest="json_path")
    ap.add_argument("--golden", dest="golden_path")
    args = ap.parse_args(argv)
    db = SessionLocal()
    try:
        report = run(db, args.document_type, args.min_n, args.golden_path)
    finally:
        db.close()
    print(report["header"])
    print("\n== Per-field agreement ==")
    for r in report["per_field"]:
        flag = "" if r["enough"] else "  (insufficient data)"
        print(f"  {r['document_type']}/{r['field_name']}: {r['correct_rate']:.0%} "
              f"(n={r['n']}, wilson≥{r['wilson_low']:.0%}){flag}")
    print("\n== Reliability (confidence bucket → observed agreement) ==")
    for b in report["reliability"]:
        rate = "n/a" if b["observed_correct_rate"] is None else f"{b['observed_correct_rate']:.0%}"
        flag = "" if b["enough"] else "  (insufficient data)"
        print(f"  conf {b['bucket']}: {rate} (n={b['n']}){flag}")
    g = report["grounded_but_wrong"]
    grate = "n/a" if g["rate"] is None else f"{g['rate']:.0%}"
    print(f"\n== Grounded-but-wrong (conf>=0.9 yet later corrected): {grate} "
          f"({g['n_wrong']}/{g['n_high_conf']}) ==")
    if args.json_path:
        with open(args.json_path, "w") as fh:
            json.dump(report, fh, indent=2)
        print(f"\nWrote {args.json_path}")


if __name__ == "__main__":
    main()
