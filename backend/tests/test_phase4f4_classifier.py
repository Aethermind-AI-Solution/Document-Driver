import asyncio
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import SchemaDefinition, Document
from app import config
from app.agents import classifier as clf
from app.agents.base import PipelineContext


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("Event loop is closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@pytest.fixture
def ctx(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'c.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    doc = Document(filename="a.pdf", document_type="unknown", stored_path="a", status="processing")
    db.add(doc); db.commit(); db.refresh(doc)
    c = PipelineContext(db=db, document=doc, hint_type="unknown")
    c.pages = [{"text": "a novel kind of document"}]
    yield c
    db.close()


def test_low_confidence_triggers_author(ctx, monkeypatch):
    monkeypatch.setattr(config, "SCHEMA_AUTHOR_ENABLED", True)

    async def fake_classify(text, keys):
        return None, 0.0
    monkeypatch.setattr(clf, "classify_document", fake_classify)

    async def fake_propose(text, existing):
        return {"key": "manifest", "name": "Manifest",
                "fields": [{"name": "a", "label": "A", "type": "string", "required": False}]}
    monkeypatch.setattr(clf, "propose_schema", fake_propose)

    _run(clf.ClassifierAgent().run(ctx))
    assert ctx.document.document_type == "manifest"
    assert ctx.schema["name"] == "Manifest"
    assert ctx.db.query(SchemaDefinition).filter_by(key="manifest", status="suggested").count() == 1


def test_confident_classify_does_not_author(ctx, monkeypatch):
    monkeypatch.setattr(config, "SCHEMA_AUTHOR_ENABLED", True)

    async def fake_classify(text, keys):
        return "invoice", 0.95
    monkeypatch.setattr(clf, "classify_document", fake_classify)

    called = {"v": False}
    async def fake_propose(text, existing):
        called["v"] = True
        return None
    monkeypatch.setattr(clf, "propose_schema", fake_propose)

    _run(clf.ClassifierAgent().run(ctx))
    assert ctx.document.document_type == "invoice"
    assert called["v"] is False


def test_disabled_flag_never_authors(ctx, monkeypatch):
    monkeypatch.setattr(config, "SCHEMA_AUTHOR_ENABLED", False)

    async def fake_classify(text, keys):
        return None, 0.0
    monkeypatch.setattr(clf, "classify_document", fake_classify)

    called = {"v": False}
    async def fake_propose(text, existing):
        called["v"] = True
        return None
    monkeypatch.setattr(clf, "propose_schema", fake_propose)

    _run(clf.ClassifierAgent().run(ctx))
    assert called["v"] is False
    assert ctx.document.document_type == "invoice"      # unchanged fallback
