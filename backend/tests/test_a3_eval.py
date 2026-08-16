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
