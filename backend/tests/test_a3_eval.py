from app import services


def test_document_confidence_min_over_required():
    schema = [{"name": "total", "required": True}, {"name": "notes"}]
    fields = [{"field_name": "total", "confidence": 0.4}, {"field_name": "notes", "confidence": 0.95}]
    assert services.document_confidence(fields, schema) == 0.4


def test_document_confidence_wrong_required_not_averaged_away():
    schema = [{"name": "total", "required": True}, {"name": "a"}, {"name": "b"}, {"name": "c"}]
    fields = [{"field_name": "total", "confidence": 0.4}] + \
             [{"field_name": n, "confidence": 0.95} for n in ("a", "b", "c")]
    assert services.document_confidence(fields, schema) == 0.4


def test_document_confidence_fallback_mean_when_no_required():
    schema = [{"name": "a"}, {"name": "b"}]
    fields = [{"field_name": "a", "confidence": 0.8}, {"field_name": "b", "confidence": 0.6}]
    assert abs(services.document_confidence(fields, schema) - 0.7) < 1e-9


def test_document_confidence_empty_is_one():
    assert services.document_confidence([], []) == 1.0


from app import eval as evalmod
from app.eval import EvalRecord


def _rec(dt="invoice", fn="total", required=True, conf=0.95, grounded="grounded", correct=True, source="corrections"):
    return EvalRecord(dt, fn, required, conf, grounded, correct, source)


def test_wilson_lower_bound():
    assert evalmod.wilson_lower_bound(0, 0) == 0.0
    assert 0.44 < evalmod.wilson_lower_bound(8, 10) < 0.50
    # more samples at the same rate → tighter (higher) lower bound
    assert evalmod.wilson_lower_bound(80, 100) > evalmod.wilson_lower_bound(8, 10)


def test_per_field_accuracy_groups_and_min_n():
    recs = [_rec(correct=True)] * 3 + [_rec(correct=False)]
    out = evalmod.per_field_accuracy(recs, min_n=30)
    row = next(r for r in out if r["field_name"] == "total")
    assert row["n"] == 4 and abs(row["correct_rate"] - 0.75) < 1e-9 and row["enough"] is False
    assert row["required"] is True


def test_reliability_table_buckets_align_to_ground_constants():
    recs = [_rec(conf=0.95, correct=True), _rec(conf=0.95, correct=False), _rec(conf=0.40, correct=False)]
    table = evalmod.reliability_table(recs, min_n=1)
    b95 = next(b for b in table if b["bucket"] == "0.95")
    assert b95["n"] == 2 and abs(b95["observed_correct_rate"] - 0.5) < 1e-9
    b40 = next(b for b in table if b["bucket"] == "0.40")
    assert b40["n"] == 1 and b40["observed_correct_rate"] == 0.0
    # empty bucket → n 0, rate None
    b70 = next(b for b in table if b["bucket"] == "0.70")
    assert b70["n"] == 0 and b70["observed_correct_rate"] is None


def test_grounded_but_wrong_rate():
    recs = [_rec(conf=0.95, correct=True), _rec(conf=0.95, correct=False), _rec(conf=0.5, correct=False)]
    gbw = evalmod.grounded_but_wrong_rate(recs)
    assert gbw["n_high_conf"] == 2 and gbw["n_wrong"] == 1 and abs(gbw["rate"] - 0.5) < 1e-9


def test_build_report_shape():
    rep = evalmod.build_report([_rec()], min_n=1)
    assert set(rep) >= {"n", "per_field", "reliability", "grounded_but_wrong", "header"}
    assert "upper bound" in rep["header"].lower()


from app.models import Document, ExtractedField


def _approved_doc(db, dt="invoice"):
    d = Document(filename="a.pdf", document_type=dt, stored_path="p", status="approved", review_required=False)
    db.add(d); db.commit(); db.refresh(d)
    return d


def test_correction_records_labels_correct(db_session):
    d = _approved_doc(db_session)
    # edited (wrong) required field, and an unedited field (presumed correct)
    db_session.add(ExtractedField(document_id=d.id, field_name="total", field_value="100",
                                  original_value="90", edited_by_user=True, confidence=0.95, grounded="grounded"))
    db_session.add(ExtractedField(document_id=d.id, field_name="vendor_name", field_value="Acme",
                                  original_value="Acme", edited_by_user=False, confidence=0.9, grounded="grounded"))
    db_session.commit()
    recs = evalmod.correction_records(db_session)
    by = {r.field_name: r for r in recs}
    assert by["total"].correct is False and by["total"].required is True
    assert by["vendor_name"].correct is True


def test_correction_records_excludes_non_approved(db_session):
    d = Document(filename="b.pdf", document_type="invoice", stored_path="p", status="review_required")
    db_session.add(d); db_session.commit(); db_session.refresh(d)
    db_session.add(ExtractedField(document_id=d.id, field_name="total", field_value="1",
                                  original_value="1", confidence=0.9)); db_session.commit()
    assert evalmod.correction_records(db_session) == []


def test_golden_records_compares_to_expected(db_session):
    d = _approved_doc(db_session)
    db_session.add(ExtractedField(document_id=d.id, field_name="total", field_value="100",
                                  original_value="100", confidence=0.95, grounded="grounded"))
    db_session.add(ExtractedField(document_id=d.id, field_name="invoice_number", field_value="INV-1",
                                  original_value="INV-1", confidence=0.9, grounded="grounded"))
    db_session.commit()
    fixture = {"documents": [{"document_id": d.id, "expected": {"total": "100", "invoice_number": "WRONG"}}]}
    recs = {r.field_name: r for r in evalmod.golden_records(db_session, fixture)}
    assert recs["total"].correct is True and recs["total"].source == "golden"
    assert recs["invoice_number"].correct is False


import importlib.util
from pathlib import Path

_CLI = Path(__file__).resolve().parents[1] / "scripts" / "eval.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("a3_eval_cli", _CLI)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def test_cli_run_builds_report(db_session, tmp_path):
    d = _approved_doc(db_session)
    db_session.add(ExtractedField(document_id=d.id, field_name="total", field_value="1",
                                  original_value="2", edited_by_user=True, confidence=0.95, grounded="grounded"))
    db_session.commit()
    cli = _load_cli()
    report = cli.run(db_session, min_n=1)
    assert report["n"] == 1 and "upper bound" in report["header"].lower()
    assert report["grounded_but_wrong"]["n_wrong"] == 1
