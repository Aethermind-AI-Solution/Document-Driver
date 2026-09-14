from app.models import Document

def test_roi_reports_stp_and_counts(client, db_session):
    db_session.add_all([
        Document(filename="a", document_type="invoice", stored_path="a", org_id=1,
                 status="approved", auto_approved=True, confidence=0.99, processing_time=3.0),
        Document(filename="b", document_type="invoice", stored_path="b", org_id=1,
                 status="approved", auto_approved=False, confidence=0.8, processing_time=5.0),
        Document(filename="c", document_type="invoice", stored_path="c", org_id=1,
                 status="review_required", auto_approved=False, confidence=0.7, processing_time=7.0),
    ])
    db_session.commit()
    r = client.get("/admin/roi")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 3 and data["auto_approved"] == 1
    assert round(data["stp_rate"], 3) == round(1/3, 3)
    assert data["avg_review_seconds"] == 6.0  # mean of 5.0 and 7.0 (non-auto-approved)
