from app.calibration import separation

def test_separation_reports_margin_between_correct_and_wrong():
    records = [
        {"confidence": 0.95, "correct": True},
        {"confidence": 0.90, "correct": True},
        {"confidence": 0.40, "correct": False},
        {"confidence": 0.55, "correct": False},
    ]
    out = separation(records)
    assert out["n"] == 4
    assert out["mean_correct"] == 0.925
    assert out["mean_wrong"] == 0.475
    assert round(out["margin"], 3) == 0.45

def test_separation_handles_single_class():
    out = separation([{"confidence": 0.9, "correct": True}])
    assert out["mean_wrong"] is None and out["margin"] is None
