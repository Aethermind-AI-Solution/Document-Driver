from app.models import Document, ExtractedField


def _seed(session):
    doc = Document(filename="x.pdf", document_type="invoice", status="processed",
                   review_required=True, stored_path="/tmp/x.pdf")
    session.add(doc)
    session.flush()
    session.add(ExtractedField(document_id=doc.id, field_name="total",
                               field_value="100", confidence=0.7, validated=True))
    session.commit()
    return doc.id


def _put(client, doc_id, action, reason=None, value="100"):
    return client.put(f"/document/{doc_id}", json={
        "fields": [{"field_name": "total", "field_value": value, "validated": True}],
        "action": action, "reason": reason,
    })


def test_reject_sets_status_and_logs_reason(client, db_session):
    doc_id = _seed(db_session)
    resp = _put(client, doc_id, "reject", reason="blurry scan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert body["review_required"] is False
    rejected = [a for a in body["audit"] if a["action"] == "Rejected"]
    assert len(rejected) == 1 and rejected[0]["details"] == "blurry scan"


def test_reject_without_reason_uses_default(client, db_session):
    doc_id = _seed(db_session)
    body = _put(client, doc_id, "reject").json()
    rejected = [a for a in body["audit"] if a["action"] == "Rejected"]
    assert rejected[0]["details"] == "No reason given"


def test_save_keeps_status_pending(client, db_session):
    doc_id = _seed(db_session)
    body = _put(client, doc_id, "save", value="120").json()
    assert body["status"] == "processed"
    assert body["review_required"] is True
    assert any(a["action"] == "Edited" for a in body["audit"])
    assert [f for f in body["fields"] if f["field_name"] == "total"][0]["field_value"] == "120"


def test_approve_still_approves(client, db_session):
    doc_id = _seed(db_session)
    body = _put(client, doc_id, "approve").json()
    assert body["status"] == "approved"
    assert body["review_required"] is False
    assert any(a["action"] == "Approved" for a in body["audit"])
