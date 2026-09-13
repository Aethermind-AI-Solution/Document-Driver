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
