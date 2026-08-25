import io
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
BACKEND = Path(__file__).resolve().parents[1]

def test_migration_0009_org_backfill(tmp_path):
    url = f"sqlite:///{tmp_path / 'o.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "0008_document_delivery_flags")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("INSERT INTO users (email,password_hash,role,is_active,created_at) "
                       "VALUES ('a@b.co','x','admin',1,CURRENT_TIMESTAMP)"))
    command.upgrade(cfg, "head")
    insp = inspect(eng)
    assert "organizations" in insp.get_table_names()
    with eng.connect() as c:
        assert c.execute(text("SELECT id FROM organizations")).scalar() == 1
        assert c.execute(text("SELECT org_id FROM users WHERE email='a@b.co'")).scalar() == 1


def test_migration_0009_downgrade_upgrade_roundtrip(tmp_path):
    """0009 must round-trip cleanly (down past it, then back up) without erroring,
    covering both the schema_definitions unique-constraint swap and its reversal."""
    url = f"sqlite:///{tmp_path / 'r.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0008_document_delivery_flags")
    eng = create_engine(url)
    insp = inspect(eng)
    assert "organizations" not in insp.get_table_names()
    command.upgrade(cfg, "head")
    insp = inspect(eng)
    assert "organizations" in insp.get_table_names()
    uq_names = {uc["name"] for uc in insp.get_unique_constraints("schema_definitions")}
    assert "uq_schema_definitions_org_id_key" in uq_names


# ---- Task 2: org context var + write-path stamping ------------------------

def test_upload_stamps_document_org_id(client, db_session):
    """The client fixture authenticates as an admin with org_id=1; upload must
    stamp the new Document with that org."""
    from app.models import Document
    r = client.post("/upload", files={"file": ("a.pdf", io.BytesIO(b"%PDF"), "application/pdf")})
    assert r.status_code == 201
    doc = db_session.query(Document).filter_by(id=r.json()["id"]).one()
    assert doc.org_id == 1


def test_upload_audit_log_stamped_from_context(client, db_session):
    """services.log() stamps org_id from the ambient context var, which the
    client fixture sets to the authenticated admin's org (1)."""
    from app.models import AuditLog
    r = client.post("/upload", files={"file": ("a.pdf", io.BytesIO(b"%PDF"), "application/pdf")})
    assert r.status_code == 201
    entry = db_session.query(AuditLog).filter_by(document_id=r.json()["id"], action="Uploaded").one()
    assert entry.org_id == 1


def test_log_stamps_org_id_from_context(db_session):
    """services.log() stamps whatever org_id is active in context at call time.
    (Self-contained: sets context explicitly rather than assuming a default; the
    autouse fixture handles reset. Queries with skip_org_filter so it holds once
    the fail-closed loader-criteria is live.)"""
    from app.context import set_current_org
    from app.models import Document, AuditLog
    from app.services import log
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x", org_id=42)
    db_session.add(doc); db_session.commit(); db_session.refresh(doc)

    set_current_org(42)
    log(db_session, doc.id, "Uploaded", "unit test", actor=None)
    db_session.commit()

    entry = (db_session.query(AuditLog).filter_by(document_id=doc.id, action="Uploaded")
             .execution_options(skip_org_filter=True).one())
    assert entry.org_id == 42


def test_pipeline_stamps_extracted_field_org_id(db_session, monkeypatch):
    """ExtractedField rows created by the pipeline inherit the owning
    document's org_id (not the ambient context)."""
    import asyncio
    from app import services
    from app.agents import pipeline, classifier
    from app.models import Document, ExtractedField

    async def fake_classify(text, keys):
        return ("invoice", 0.99)
    monkeypatch.setattr(classifier, "classify_document", fake_classify)
    monkeypatch.setattr(services, "ai_extract", lambda text, fields, path, hints=None: [
        {"field_name": f["name"], "field_value": "100", "source_quote": None,
         "grounded": "grounded", "confidence": 0.95} for f in fields])
    monkeypatch.setattr("app.agents.pipeline.split_pages",
                        lambda data, suffix: [{"index": 0, "pdf_bytes": b"", "text": "Total 100"}])
    monkeypatch.setattr("app.storage.get_storage",
                        lambda: type("S", (), {"open": lambda self, k: b"bytes"})())

    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x.pdf", org_id=7)
    db_session.add(doc); db_session.commit(); db_session.refresh(doc)

    asyncio.run(pipeline.run_pipeline(db_session, doc, "invoice"))

    # Verification query: the ambient test context is org 1 (autouse default),
    # but this row belongs to org 7 (inherited from the document, not
    # context) — skip_org_filter to inspect it directly, same pattern as
    # test_log_stamps_org_id_from_context above.
    field = (db_session.query(ExtractedField).filter_by(document_id=doc.id, field_name="total")
             .execution_options(skip_org_filter=True).first())
    assert field is not None and field.org_id == 7


def test_get_correction_hints_takes_org_id(db_session):
    """get_correction_hints now requires an explicit org_id positional arg and
    scopes the underlying query to it. Build each org's rows under that org's
    own context (org_id is stamped on ExtractedField from ambient context,
    same as production — see services.log()/pipeline), then read back under
    matching context, mirroring the real call site (extractor.py always has
    ambient context == ctx.document.org_id)."""
    from app.context import set_current_org
    from app.models import Document, ExtractedField
    from app.services import get_correction_hints

    set_current_org(1)
    doc_org1 = Document(filename="a", document_type="invoice", stored_path="a",
                        status="approved", org_id=1)
    db_session.add(doc_org1); db_session.flush()
    db_session.add(ExtractedField(document_id=doc_org1.id, field_name="total",
                                  original_value="1,00", field_value="100",
                                  edited_by_user=True, confidence=0.9))
    db_session.commit()

    set_current_org(2)
    doc_org2 = Document(filename="b", document_type="invoice", stored_path="b",
                        status="approved", org_id=2)
    db_session.add(doc_org2); db_session.flush()
    db_session.add(ExtractedField(document_id=doc_org2.id, field_name="total",
                                  original_value="9,00", field_value="900",
                                  edited_by_user=True, confidence=0.9))
    db_session.commit()

    fields = [{"name": "total"}]
    set_current_org(1)
    assert get_correction_hints(db_session, 1, "invoice", fields) == {"total": [("1,00", "100")]}
    set_current_org(2)
    assert get_correction_hints(db_session, 2, "invoice", fields) == {"total": [("9,00", "900")]}


# ---- Task 4: fail-closed with_loader_criteria scoping ----------------------

def test_query_without_org_context_raises(db_session):
    """With org context unset, any SELECT against a _TenantMixin model must
    raise RuntimeError rather than silently returning cross-org (or all) rows.
    The autouse fixture defaults context to org 1, so reset it to None here
    to exercise the fail-closed path."""
    import pytest
    from app.context import set_current_org
    from app.models import Document

    set_current_org(None)
    with pytest.raises(RuntimeError):
        db_session.query(Document).all()
    with pytest.raises(RuntimeError):
        db_session.query(Document).first()


def test_query_with_org_context_scopes_rows(db_session):
    """With org context set, queries only return rows for that org — rows
    belonging to other orgs are filtered out entirely (not just hidden by id)."""
    from app.context import set_current_org
    from app.models import Document

    set_current_org(1)
    doc1 = Document(filename="a", document_type="invoice", stored_path="a", org_id=1)
    db_session.add(doc1); db_session.commit()

    set_current_org(2)
    doc2 = Document(filename="b", document_type="invoice", stored_path="b", org_id=2)
    db_session.add(doc2); db_session.commit()

    set_current_org(1)
    rows = db_session.query(Document).all()
    assert [d.id for d in rows] == [doc1.id]

    set_current_org(2)
    rows = db_session.query(Document).all()
    assert [d.id for d in rows] == [doc2.id]
