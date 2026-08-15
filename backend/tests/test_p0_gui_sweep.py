from app.models import Document


def _add_docs(db, n, status="processed", review=False, ptime=1.0):
    for i in range(n):
        db.add(Document(filename=f"d{i}.pdf", document_type="invoice", stored_path=f"p{i}",
                        status=status, review_required=review, processing_time=ptime))
    db.commit()


def test_documents_paginated_shape(client, db_session):
    _add_docs(db_session, 5)
    r = client.get("/documents?limit=2&offset=0")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5 and len(body["items"]) == 2
    # slim shape: no heavy keys
    assert "fields" not in body["items"][0] and "audit" not in body["items"][0]
    assert {"id", "filename", "status", "review_required"} <= set(body["items"][0])


def test_documents_offset_and_filters(client, db_session):
    _add_docs(db_session, 3, status="processed")
    _add_docs(db_session, 2, status="approved")
    # status filter
    r = client.get("/documents?status=approved")
    assert r.json()["total"] == 2
    # offset past first page
    r2 = client.get("/documents?limit=2&offset=2")
    assert len(r2.json()["items"]) >= 1
    # q filter (filename ilike)
    r3 = client.get("/documents?q=d0")
    assert r3.json()["total"] >= 1


def test_documents_stats(client, db_session):
    _add_docs(db_session, 2, status="processed", review=False, ptime=2.0)
    _add_docs(db_session, 1, status="review_required", review=True, ptime=4.0)
    s = client.get("/documents/stats").json()
    assert s["total"] == 3 and s["review_required"] == 1
    assert abs(s["avg_processing_time"] - (2.0 + 2.0 + 4.0) / 3) < 0.01
