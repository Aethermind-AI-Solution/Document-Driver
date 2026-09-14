import asyncio
from app import services
from app.agents import pipeline, classifier
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


def test_clean_duplicate_forces_review_required(db_session, monkeypatch):
    """A duplicate with no other anomalies and all-good fields must still be
    flagged for review (regression: review_required once read ctx.anomalies
    instead of the merged document.anomalies, so a clean-but-duplicate
    document silently sailed through as "processed"). Drives the REAL
    run_pipeline end-to-end (same offline-stub harness as
    test_phase2_pipeline.py) rather than re-implementing the expression."""
    from app.context import set_current_org
    set_current_org(1)

    # Prior document in org 1 whose fingerprint the doc-under-test will collide
    # with once extraction resolves the same vendor/invoice_number/total/date.
    prior_fields = [{"field_name": "vendor_name", "field_value": "Acme Supplies"},
                    {"field_name": "invoice_number", "field_value": "INV-1042"},
                    {"field_name": "total", "field_value": "100.00"},
                    {"field_name": "invoice_date", "field_value": "2026-07-01"}]
    prior = Document(filename="original.pdf", document_type="invoice", stored_path="original.pdf",
                     org_id=1, fingerprint=services.compute_fingerprint(prior_fields))
    db_session.add(prior); db_session.commit()

    async def fake_classify(text, keys):
        return ("invoice", 0.99)
    monkeypatch.setattr(classifier, "classify_document", fake_classify)

    extracted = {"vendor_name": "Acme Supplies", "invoice_number": "INV-1042",
                "total": "100.00", "invoice_date": "2026-07-01"}
    monkeypatch.setattr(services, "ai_extract", lambda text, fields, path, hints=None: [
        {"field_name": f["name"], "field_value": extracted.get(f["name"]),
         "source_quote": None,
         "grounded": "grounded" if f["name"] in extracted else "absent",
         "confidence": 0.99} for f in fields])
    monkeypatch.setattr("app.agents.pipeline.split_pages",
                        lambda data, suffix: [{"index": 0, "pdf_bytes": b"", "text": "Total 100"}])
    monkeypatch.setattr("app.storage.get_storage",
                        lambda: type("S", (), {"open": lambda self, k: b"bytes"})())

    current = Document(filename="copy.pdf", document_type="invoice", stored_path="copy.pdf", org_id=1)
    db_session.add(current); db_session.commit(); db_session.refresh(current)

    result = asyncio.run(pipeline.run_pipeline(db_session, current, "invoice"))

    assert result.review_required is True
    assert result.status == "review_required"
    assert result.anomalies and any(a.get("type") == "duplicate_invoice" for a in result.anomalies)


def test_find_duplicate_does_not_match_across_orgs(db_session):
    """services.find_duplicate must never match a same-fingerprint document
    living in a different org (fold-in regression for the org-scoping guard
    on duplicate detection)."""
    from app.context import set_current_org
    from app.models import Organization
    fields = [{"field_name": "vendor_name", "field_value": "Acme"},
              {"field_name": "invoice_number", "field_value": "INV-9"},
              {"field_name": "total", "field_value": "50.00"},
              {"field_name": "invoice_date", "field_value": "2026-01-01"}]
    fp = services.compute_fingerprint(fields)

    set_current_org(1)
    org1_doc = Document(filename="o1.png", document_type="invoice", stored_path="o1", org_id=1, fingerprint=fp)
    db_session.add(org1_doc); db_session.commit()

    db_session.add(Organization(id=2, name="Org Two")); db_session.commit()
    set_current_org(2)
    org2_doc = Document(filename="o2.png", document_type="invoice", stored_path="o2", org_id=2, fingerprint=fp)
    db_session.add(org2_doc); db_session.commit()

    assert services.find_duplicate(db_session, org2_doc) is None
