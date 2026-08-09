import types
from app import jobs
from app.models import Document, AuditLog


def test_reset_stuck_processing_flips_only_processing(db_session):
    d1 = Document(filename="a.pdf", document_type="invoice", stored_path="a", status="processing")
    d2 = Document(filename="b.pdf", document_type="invoice", stored_path="b", status="processed")
    db_session.add_all([d1, d2]); db_session.commit()
    n = jobs.reset_stuck_processing(db_session)
    assert n == 1
    db_session.refresh(d1); db_session.refresh(d2)
    assert d1.status == "error" and d2.status == "processed"
    assert db_session.query(AuditLog).filter_by(document_id=d1.id, action="Processing failed").count() == 1


def test_run_pipeline_task_runs_and_closes(monkeypatch):
    closed = {"v": False}
    fake_doc = types.SimpleNamespace(id=1, document_type="invoice")

    class FakeSession:
        def get(self, model, ident): return fake_doc if ident == 1 else None
        def close(self): closed["v"] = True

    monkeypatch.setattr(jobs, "SessionLocal", lambda: FakeSession())
    seen = {}

    async def fake_run(db, doc, hint, actor=None):
        seen["doc"] = doc; seen["hint"] = hint; seen["actor"] = actor

    monkeypatch.setattr(jobs, "run_pipeline", fake_run)
    jobs.run_pipeline_task(1, None)
    assert seen["doc"] is fake_doc and seen["hint"] == "invoice" and seen["actor"] is None
    assert closed["v"] is True


def test_run_pipeline_task_swallows_errors(monkeypatch):
    closed = {"v": False}
    fake_doc = types.SimpleNamespace(id=1, document_type="invoice")

    class FakeSession:
        def get(self, model, ident): return fake_doc
        def close(self): closed["v"] = True

    monkeypatch.setattr(jobs, "SessionLocal", lambda: FakeSession())

    async def boom(db, doc, hint, actor=None): raise RuntimeError("kaboom")

    monkeypatch.setattr(jobs, "run_pipeline", boom)
    jobs.run_pipeline_task(1, None)   # must NOT raise
    assert closed["v"] is True
