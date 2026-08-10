import pytest
import asyncio
import base64
from app import mcp_server
from app.models import Document, ExtractedField, AuditLog


def test_list_types_impl(db_session):
    out = mcp_server.list_types_impl(db_session)
    keys = {t["key"] for t in out}
    assert "invoice" in keys and all("name" in t for t in out)


def test_get_document_impl_roundtrip(db_session):
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x", status="processed", confidence=0.9)
    db_session.add(doc); db_session.flush()
    db_session.add(ExtractedField(document_id=doc.id, field_name="total", field_value="500",
                                  original_value="500", confidence=0.95, grounded="grounded"))
    db_session.commit()
    out = mcp_server.get_document_impl(db_session, doc.id)
    assert out["document_id"] == doc.id and out["fields"][0]["field_value"] == "500"


def test_get_document_impl_missing_raises(db_session):
    with pytest.raises(ValueError):
        mcp_server.get_document_impl(db_session, 99999)


def test_submit_correction_impl_updates_and_audits(db_session):
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x", status="review_required")
    db_session.add(doc); db_session.flush()
    db_session.add(ExtractedField(document_id=doc.id, field_name="total", field_value="500",
                                  original_value="500", confidence=0.9))
    db_session.commit()
    actor = mcp_server.build_service_principal()
    out = mcp_server.submit_correction_impl(db_session, doc.id, "total", "600", actor)
    assert out["field_value"] == "600" and out["edited_by_user"] is True
    f = db_session.query(ExtractedField).filter_by(document_id=doc.id, field_name="total").first()
    assert f.field_value == "600" and f.original_value == "500"
    assert db_session.query(AuditLog).filter_by(document_id=doc.id, actor_email="mcp-service").count() == 1


def test_submit_correction_impl_missing_field_raises(db_session):
    doc = Document(filename="x.pdf", document_type="invoice", stored_path="x")
    db_session.add(doc); db_session.commit()
    with pytest.raises(ValueError):
        mcp_server.submit_correction_impl(db_session, doc.id, "nope", "x", mcp_server.build_service_principal())


def test_extract_document_impl_happy(db_session, monkeypatch):
    from app import mcp_server, storage
    from app.agents import pipeline as pl

    async def fake_run(db, doc, hint, actor=None):
        doc.status = "processed"; doc.confidence = 0.9
        db.add(ExtractedField(document_id=doc.id, field_name="total", field_value="500",
                              original_value="500", confidence=0.95, grounded="grounded"))
        db.commit(); db.refresh(doc); return doc

    monkeypatch.setattr(mcp_server, "run_pipeline", fake_run)
    monkeypatch.setattr(storage, "get_storage",
                        lambda: type("S", (), {"save": lambda self, k, d: k})())
    actor = mcp_server.build_service_principal()
    out = asyncio.run(mcp_server.extract_document_impl(
        db_session, base64.b64encode(b"%PDF-1.4").decode(), "a.pdf", "auto", actor))
    assert out["status"] == "processed" and out["fields"][0]["field_value"] == "500"


def test_extract_document_impl_bad_base64(db_session):
    from app import mcp_server
    with pytest.raises(ValueError):
        asyncio.run(mcp_server.extract_document_impl(
            db_session, "!!!not-base64!!!", "a.pdf", "auto", mcp_server.build_service_principal()))


def test_extract_document_impl_bad_suffix(db_session):
    from app import mcp_server
    with pytest.raises(ValueError):
        asyncio.run(mcp_server.extract_document_impl(
            db_session, base64.b64encode(b"x").decode(), "a.exe", "auto", mcp_server.build_service_principal()))
