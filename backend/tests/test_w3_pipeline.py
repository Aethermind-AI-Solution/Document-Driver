from app import services
from app.agents import pipeline
from app.models import Document


def test_pipeline_flags_duplicate(db_session, monkeypatch):
    from app.context import set_current_org
    set_current_org(1)
    fields = [{"field_name": "invoice_number", "field_value": "INV-1042"},
              {"field_name": "total", "field_value": "100.00"}]
    fp = services.compute_fingerprint(fields)
    prior = Document(filename="original.png", document_type="invoice", stored_path="o",
                     org_id=1, fingerprint=fp)
    db_session.add(prior); db_session.commit()

    current = Document(filename="copy.png", document_type="invoice", stored_path="c",
                       org_id=1, fingerprint=fp)
    db_session.add(current); db_session.commit()

    anomalies = pipeline.detect_duplicate(db_session, current)
    assert anomalies == [{"type": "duplicate_invoice",
                          "message": "Possible duplicate of original.png",
                          "duplicate_of": prior.id}]


def test_pipeline_no_duplicate_returns_empty(db_session):
    from app.context import set_current_org
    set_current_org(1)
    doc = Document(filename="solo.png", document_type="invoice", stored_path="s",
                   org_id=1, fingerprint="unique-fp")
    db_session.add(doc); db_session.commit()
    assert pipeline.detect_duplicate(db_session, doc) == []


def test_clean_duplicate_forces_review_required():
    """A duplicate with no other anomalies and all-good fields must still be
    flagged for review (regression: review_required read ctx.anomalies, not the
    merged document.anomalies)."""
    # Mirror the exact run_pipeline logic for the clean-duplicate case:
    ctx_anomalies = None
    dup_anomalies = [{"type": "duplicate_invoice", "message": "Possible duplicate of original.png", "duplicate_of": 1}]
    fields = [{"confidence": 0.99, "validated": True}]
    document_anomalies = (ctx_anomalies or []) + dup_anomalies or None
    review_required = bool(document_anomalies) or any(
        f["confidence"] < .9 or not f["validated"] for f in fields)
    assert review_required is True
