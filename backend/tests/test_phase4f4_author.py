import asyncio
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import SchemaDefinition, AuditLog, Document, Organization
from app.agents import schema_author


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("Event loop is closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


class FakeResp:
    def __init__(self, text): self.output_text = text


def _stub_client(monkeypatch, output_text):
    class FakeClient:
        def __init__(self, *a, **k): pass
        class responses:
            pass
    fake = FakeClient()

    async def create(**kwargs):
        return FakeResp(output_text)
    fake.responses = type("R", (), {"create": staticmethod(create)})()
    monkeypatch.setattr(schema_author, "_client", lambda: fake)


def test_propose_normalizes_and_returns(monkeypatch):
    _stub_client(monkeypatch, '{"key":"Shipping Manifest","name":"Shipping Manifest",'
                              '"fields":[{"name":"Carrier Name","label":"Carrier","type":"string","required":true},'
                              '{"name":"weird","label":"W","type":"bogus","required":false}]}')
    out = _run(schema_author.propose_schema("some text", set()))
    assert out["key"] == "shipping-manifest"
    names = {f["name"] for f in out["fields"]}
    assert "carrier_name" in names                 # field name normalized
    assert all(f["type"] in {"string", "number", "date", "array"} for f in out["fields"])  # bogus -> string


def test_propose_uniquifies_key(monkeypatch):
    _stub_client(monkeypatch, '{"key":"invoice","name":"Invoice-like",'
                              '"fields":[{"name":"a","label":"A","type":"string","required":false}]}')
    out = _run(schema_author.propose_schema("t", {"invoice"}))
    assert out["key"] == "invoice-2"


def test_propose_none_on_empty_fields(monkeypatch):
    _stub_client(monkeypatch, '{"key":"x","name":"X","fields":[]}')
    assert _run(schema_author.propose_schema("t", set())) is None


def test_propose_none_on_too_many_fields(monkeypatch):
    fields = ",".join('{"name":"f%d","label":"F","type":"string","required":false}' % i
                      for i in range(26))
    _stub_client(monkeypatch, '{"key":"x","name":"X","fields":[%s]}' % fields)
    assert _run(schema_author.propose_schema("t", set())) is None


def test_propose_none_on_exception(monkeypatch):
    def boom():
        raise RuntimeError("no client")
    monkeypatch.setattr(schema_author, "_client", boom)
    assert _run(schema_author.propose_schema("t", set())) is None


def test_persist_suggested_creates_row_and_audit(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'p.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    org = Organization(name="Test Org")
    db.add(org); db.commit(); db.refresh(org)
    doc = Document(filename="a.pdf", document_type="unknown", stored_path="a", status="processing", org_id=org.id)
    db.add(doc); db.commit(); db.refresh(doc)
    proposal = {"key": "manifest", "name": "Manifest",
                "fields": [{"name": "a", "label": "A", "type": "string", "required": False}]}
    row = schema_author.persist_suggested(db, proposal, doc.id, org_id=org.id)
    db.commit()
    assert row.id and row.status == "suggested" and row.origin_document_id == doc.id and row.org_id == org.id
    assert db.query(AuditLog).filter_by(action="Schema suggested").count() == 1
    db.close()
