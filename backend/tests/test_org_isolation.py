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


# ---- Task 4: middleware sets org context (survives per-dependency threadpool) --

def test_real_token_scopes_documents_to_its_own_org(db_session):
    """End-to-end proof that the ASGI org-context middleware (not the
    get_current_user dependency-override used by the `client` fixture, and not
    the autouse `_default_org_context` fixture) is what scopes tenant queries
    on a REAL HTTP request.

    Builds a plain TestClient with NO dependency overrides, mints a real JWT
    via auth.create_access_token, and asserts /documents returns only the
    calling org's row. Explicitly resets context to None first so a pass here
    cannot be explained by the autouse fixture's ambient org-1 context — only
    the middleware (which runs in the request's own Task context and is
    therefore inherited by the get_db dependency and the endpoint body, unlike
    a ContextVar.set() inside the sync get_current_user dependency) can be
    responsible for a 200 with correctly-scoped rows.
    """
    from fastapi.testclient import TestClient
    from app import auth, context
    from app.main import app
    from app.models import Organization, User, Document

    # Prove the middleware — not the autouse fixture — sets context.
    context.set_current_org(None)

    org2 = Organization(id=2, name="Org Two")
    db_session.add(org2); db_session.commit()

    # Row creation/refresh below round-trips through the fail-closed
    # loader-criteria too, so each org's rows are built under that org's own
    # context (same pattern as test_get_correction_hints_takes_org_id above).
    context.set_current_org(1)
    admin1 = User(email="admin1@test.local", password_hash="x", role="admin",
                  is_active=True, org_id=1)
    db_session.add(admin1); db_session.commit(); db_session.refresh(admin1)
    doc1 = Document(filename="org1.pdf", document_type="invoice", stored_path="org1.pdf", org_id=1)
    db_session.add(doc1); db_session.commit()
    # Mint the token while attributes are still loaded/in-context (commit()
    # expires attributes, and accessing them afterwards would trigger a lazy
    # refresh query that itself needs org context).
    tok1 = auth.create_access_token(admin1)

    context.set_current_org(2)
    admin2 = User(email="admin2@test.local", password_hash="x", role="admin",
                  is_active=True, org_id=2)
    db_session.add(admin2); db_session.commit(); db_session.refresh(admin2)
    doc2 = Document(filename="org2.pdf", document_type="invoice", stored_path="org2.pdf", org_id=2)
    db_session.add(doc2); db_session.commit()
    tok2 = auth.create_access_token(admin2)

    # Reset again — no dependency overrides are installed for this test (the
    # `client` fixture is not used), so nothing else will set context for the
    # upcoming requests except the real middleware.
    context.set_current_org(None)

    assert auth.get_current_user not in app.dependency_overrides

    plain_client = TestClient(app)

    r1 = plain_client.get("/documents", headers={"Authorization": f"Bearer {tok1}"})
    assert r1.status_code == 200
    ids1 = {item["id"] for item in r1.json()["items"]}
    assert ids1 == {doc1.id}

    r2 = plain_client.get("/documents", headers={"Authorization": f"Bearer {tok2}"})
    assert r2.status_code == 200
    ids2 = {item["id"] for item in r2.json()["items"]}
    assert ids2 == {doc2.id}


# ---- Task 5: two-org isolation matrix (the security proof) -----------------

def _seed_two_orgs(db_session):
    """Build org 1 + org 2, an admin (real JWT) in each, and — in org 2 — a
    Document, a custom approved SchemaDefinition, a WebhookConfig, an
    AutoApproveConfig, and an approved Document with an edited ExtractedField
    (for the learning-loop test). Also seeds one org-1 Document so the
    aggregate-view assertions (stats/metrics) prove "org 1's own data present,
    org 2's absent" rather than just "everything's zero".

    Row creation follows the fail-closed loader-criteria: build each org's
    rows under that org's own context (same pattern as
    test_real_token_scopes_documents_to_its_own_org above). Tokens/ids are
    captured as plain values *before* the final expunge_all() below.

    IMPORTANT: ends with db_session.expunge_all(). Without it, objects created
    here stay in this shared test session's identity map, and SQLAlchemy's
    Session.get() (used by most endpoints, e.g. db.get(Document, id)) returns
    an identity-map hit directly WITHOUT re-running do_orm_execute — silently
    bypassing the org with_loader_criteria filter and producing a false 200
    instead of 404. Verified empirically: this bypass is a pure test-fixture
    artifact (the `client`/db_session fixtures share one Session across every
    simulated "request" in a test), not a production bug — in production
    get_db() and run_pipeline_task() each open a brand-new SessionLocal() per
    request/background task, so no cross-org object is ever already resident
    in a fresh session's identity map. expunge_all() forces every subsequent
    db.get()/query() in this test to hit the DB fresh, which is what actually
    exercises (and proves) the org filter, matching production's per-request
    session isolation."""
    from app import auth, context
    from app.models import (Organization, User, Document, SchemaDefinition,
                            WebhookConfig, AutoApproveConfig, ExtractedField)

    context.set_current_org(1)
    admin1 = User(email="admin1@iso.test", password_hash=auth.hash_password("pw12345678"),
                  role="admin", is_active=True, org_id=1)
    db_session.add(admin1); db_session.commit(); db_session.refresh(admin1)
    tok1 = auth.create_access_token(admin1)  # mint while attrs are still loaded

    doc1 = Document(filename="org1.pdf", document_type="invoice", stored_path="org1.pdf", org_id=1)
    db_session.add(doc1); db_session.commit(); db_session.refresh(doc1)
    doc1_id = doc1.id

    org2 = Organization(id=2, name="Org Two")
    db_session.add(org2); db_session.commit()

    context.set_current_org(2)
    admin2 = User(email="admin2@iso.test", password_hash=auth.hash_password("pw12345678"),
                  role="admin", is_active=True, org_id=2)
    db_session.add(admin2); db_session.commit(); db_session.refresh(admin2)
    admin2_id = admin2.id
    tok2 = auth.create_access_token(admin2)

    doc2 = Document(filename="org2.pdf", document_type="invoice", stored_path="org2.pdf", org_id=2)
    db_session.add(doc2); db_session.commit(); db_session.refresh(doc2)
    doc2_id = doc2.id

    schema2 = SchemaDefinition(key="org2_custom", name="Org2 Custom",
                               fields=[{"name": "total", "label": "Total", "type": "string", "required": False}],
                               status="approved", org_id=2)
    db_session.add(schema2); db_session.commit(); db_session.refresh(schema2)
    schema2_id = schema2.id

    webhook2 = WebhookConfig(document_type="invoice", url="https://example.com/hook2", active=True, org_id=2)
    db_session.add(webhook2); db_session.commit(); db_session.refresh(webhook2)
    webhook2_id = webhook2.id

    autoapprove2 = AutoApproveConfig(document_type="invoice", enabled=True, min_confidence=0.95, org_id=2)
    db_session.add(autoapprove2); db_session.commit(); db_session.refresh(autoapprove2)
    autoapprove2_id = autoapprove2.id

    # Approved doc with a human-edited field — feeds get_correction_hints().
    doc2_learn = Document(filename="org2_learn.pdf", document_type="invoice", stored_path="org2_learn.pdf",
                          status="approved", review_required=False, org_id=2)
    db_session.add(doc2_learn); db_session.commit(); db_session.refresh(doc2_learn)
    doc2_learn_id = doc2_learn.id
    field2 = ExtractedField(document_id=doc2_learn_id, field_name="total", field_value="900",
                            original_value="9,00", confidence=0.9, edited_by_user=True, org_id=2)
    db_session.add(field2); db_session.commit()

    context.set_current_org(None)
    db_session.expunge_all()

    return {
        "tok1": tok1, "tok2": tok2,
        "doc1_id": doc1_id, "doc2_id": doc2_id, "admin2_id": admin2_id,
        "schema2_id": schema2_id, "webhook2_id": webhook2_id,
        "autoapprove2_id": autoapprove2_id, "doc2_learn_id": doc2_learn_id,
    }


def test_two_org_isolation_matrix(db_session):
    """The full REST surface, hit with org 1's REAL JWT via a plain
    TestClient (no dependency_overrides — the get_current_user override used
    by the `client` fixture, plus its autouse org-1 context, would mask a
    cross-tenant bug), must never read, write, or export org 2's rows — and
    org 1's list/aggregate views must never include them.

    Context is reset to None before seeding and again before the requests so
    a pass here can only be explained by the _org_context_mw middleware
    honoring the real token — not the autouse fixture and not a dependency
    override (asserted below)."""
    from fastapi.testclient import TestClient
    from app import auth, context
    from app.main import app

    context.set_current_org(None)
    seed = _seed_two_orgs(db_session)
    context.set_current_org(None)
    assert auth.get_current_user not in app.dependency_overrides

    h1 = {"Authorization": f"Bearer {seed['tok1']}"}
    c = TestClient(app)
    b = seed["doc2_id"]

    # --- list/aggregate views: org 1 sees its own row, never org 2's -------
    r = c.get("/documents", headers=h1)
    assert r.status_code == 200
    ids = {item["id"] for item in r.json()["items"]}
    assert ids == {seed["doc1_id"]}

    r = c.get("/documents/stats", headers=h1)
    assert r.status_code == 200
    assert r.json()["total"] == 1

    r = c.get("/admin/metrics", headers=h1)
    assert r.status_code == 200
    assert sum(r.json()["status_counts"].values()) == 1
    assert r.json()["status_counts"]["uploaded"] == 1

    # --- document endpoints --------------------------------------------------
    assert c.get(f"/document/{b}", headers=h1).status_code == 404
    assert c.put(f"/document/{b}", headers=h1, json={"fields": [], "action": "save"}).status_code == 404
    assert c.post(f"/process/{b}", headers=h1).status_code == 404
    assert c.get(f"/export/{b}", headers=h1).status_code == 404

    # --- schema endpoints ------------------------------------------------
    s = seed["schema2_id"]
    assert c.patch(f"/schemas/{s}", headers=h1, json={}).status_code == 404
    assert c.post(f"/schemas/{s}/approve", headers=h1).status_code == 404
    assert c.delete(f"/schemas/{s}", headers=h1).status_code == 404

    # --- webhook endpoints -------------------------------------------------
    w = seed["webhook2_id"]
    assert c.patch(f"/webhooks/{w}", headers=h1, json={}).status_code == 404
    assert c.delete(f"/webhooks/{w}", headers=h1).status_code == 404

    # --- auto-approve endpoints ---------------------------------------------
    a = seed["autoapprove2_id"]
    assert c.patch(f"/auto-approve/{a}", headers=h1, json={}).status_code == 404
    assert c.delete(f"/auto-approve/{a}", headers=h1).status_code == 404

    # --- user endpoint: no cross-org user mutation ---------------------------
    r = c.patch(f"/users/{seed['admin2_id']}", headers=h1, params={"role": "viewer"})
    assert r.status_code == 404


def test_get_correction_hints_never_leaks_other_org(db_session):
    """get_correction_hints(db, org_id=1, ...) must return ZERO of org 2's
    corrected values, even though org 2 has an approved doc of the same
    document_type with a human-edited field (built by _seed_two_orgs)."""
    from app import context
    from app.services import get_correction_hints

    context.set_current_org(None)
    _seed_two_orgs(db_session)

    context.set_current_org(1)
    hints = get_correction_hints(db_session, 1, "invoice", [{"name": "total"}])
    assert hints == {}


def test_auto_approve_and_webhook_lookup_never_resolve_other_org_config(db_session, monkeypatch):
    """should_auto_approve() and the PUT /document webhook-config lookup both
    query AutoApproveConfig/WebhookConfig by document_type alone, relying
    entirely on the fail-closed loader-criteria for org scoping. Prove an
    org-1 document never resolves org 2's auto-approve or webhook config for
    the same document_type — even though both exist (seeded by
    _seed_two_orgs, org_id=2)."""
    from app import config as appconfig, context
    from app.models import Document, AutoApproveConfig, WebhookConfig
    from app.services import should_auto_approve

    context.set_current_org(None)
    _seed_two_orgs(db_session)

    context.set_current_org(1)
    doc1 = Document(filename="o1.pdf", document_type="invoice", stored_path="o1.pdf",
                    status="processed", review_required=False, confidence=0.99, org_id=1)
    db_session.add(doc1); db_session.commit(); db_session.refresh(doc1)

    monkeypatch.setattr(appconfig, "AUTO_APPROVE_ENABLED", True)
    # should_auto_approve looks up AutoApproveConfig by document_type only;
    # org 2's enabled config for "invoice" must not be visible from org 1.
    assert should_auto_approve(db_session, doc1) is False

    cfg = db_session.query(AutoApproveConfig).filter_by(document_type="invoice").first()
    assert cfg is None

    # Same query shape as the webhook-config lookup in PUT /document.
    wh = db_session.query(WebhookConfig).filter_by(document_type="invoice", active=True).first()
    assert wh is None
