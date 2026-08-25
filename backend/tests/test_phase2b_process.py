import app.main as main_mod
from app.models import Document


def test_process_returns_202_and_dispatches(client, db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(main_mod, "run_pipeline_task",
                        lambda document_id, actor_id, org_id=None: calls.append((document_id, actor_id)))
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x", status="uploaded")
    db_session.add(doc); db_session.commit(); db_session.refresh(doc)

    resp = client.post(f"/process/{doc.id}")
    assert resp.status_code == 202
    assert resp.json()["status"] == "processing"
    # TestClient runs the background task after the response → the spy recorded it
    assert len(calls) == 1 and calls[0][0] == doc.id and calls[0][1] is not None


def test_process_409_when_already_processing(client, db_session, monkeypatch):
    monkeypatch.setattr(main_mod, "run_pipeline_task", lambda document_id, actor_id: None)
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x", status="processing")
    db_session.add(doc); db_session.commit(); db_session.refresh(doc)
    resp = client.post(f"/process/{doc.id}")
    assert resp.status_code == 409


def test_process_404_when_missing(client):
    assert client.post("/process/99999").status_code == 404
