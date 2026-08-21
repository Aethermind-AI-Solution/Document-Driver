# A4 / B9 — Auto-Approve (Straight-Through Processing) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a freshly-processed document clears a strict per-type gate, finalize it as `approved` with no human and push it to the webhook/ERP — default-off, behind belt-and-suspenders guardrails, with B11 reopen as the undo.

**Architecture:** Two shared seams (`services.should_auto_approve` reads already-computed confidence/review_required; `services.apply_approval` is the single "become approved" path used by human AND machine) prevent a divergent approval implementation. Auto-approve fires at pipeline finalize; `deliver_webhook` (own session, now retrying) is called directly and records delivery status on the document. Per-type `AutoApproveConfig` + a global env kill-switch gate it; the admin screen shows A3 eval evidence and blocks enabling on insufficient data.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic, Next.js 15/React 19/TS/Tailwind, pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-08-16-a4-b9-auto-approve-design.md`

## Global Constraints

- **Default-off everywhere:** global `AUTO_APPROVE_ENABLED` env (default false) AND per-type `enabled` (default false). Per-type DB flag is read fresh per document (instant kill); the env is the deploy-time belt.
- **Gate:** `AUTO_APPROVE_ENABLED` and per-type `enabled` and `not review_required` and `document.confidence >= min_confidence`. `min_confidence` **must be > 0.9** (422 otherwise).
- **Anti-drift:** `should_auto_approve` READS `document.confidence`/`document.review_required` (never recomputes). `apply_approval` is the ONLY code that sets `status="approved"` + `revision += 1` + approval audit + `can_transition` check — used by both `PUT /document` and the pipeline.
- **Machine identity:** auto-approve logs action `"Auto-approved"` with `actor=None` (NEVER the `/process` triggering user); webhook `approved_by="system:auto-approve"`; `Document.auto_approved=True` (sticky).
- **Webhook reliability:** `deliver_webhook` retries with backoff and writes `Document.webhook_status`/`webhook_detail`; never raises.
- Migrations `0007` (chains from `0006_document_revision`) and `0008` (chains from `0007`). No new dependencies.
- Keep backend (210) / frontend (36) green, net new on top. Match existing style (dense `page.tsx`; admin screens mirror `webhooks/page.tsx`).

---

### Task 1: `AutoApproveConfig` model + migration `0007` + schemas

**Files:**
- Modify: `backend/app/models.py`
- Create: `backend/alembic/versions/0007_auto_approve_configs.py`
- Modify: `backend/app/schemas.py`
- Test: `backend/tests/test_a4_autoapprove.py` (new)

**Interfaces:**
- Produces: `AutoApproveConfig(document_type unique, enabled, min_confidence, created_at)`; `AutoApproveCreate{document_type, enabled=False, min_confidence}`, `AutoApproveUpdate{enabled?, min_confidence?}`, `AutoApproveOut{id, document_type, enabled, min_confidence, created_at}`.

- [ ] **Step 1: Add the model.** In `backend/app/models.py`, after `WebhookConfig`:

```python
class AutoApproveConfig(Base):
    __tablename__ = "auto_approve_configs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_type: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    min_confidence: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
```

- [ ] **Step 2: Migration.** Create `backend/alembic/versions/0007_auto_approve_configs.py`:

```python
"""auto approve configs

Revision ID: 0007_auto_approve_configs
Revises: 0006_document_revision
Create Date: 2026-08-16 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0007_auto_approve_configs"
down_revision: Union[str, None] = "0006_document_revision"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table("auto_approve_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_type", sa.String(length=80), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("min_confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"))
    with op.batch_alter_table("auto_approve_configs", schema=None) as b:
        b.create_index(b.f("ix_auto_approve_configs_document_type"), ["document_type"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("auto_approve_configs", schema=None) as b:
        b.drop_index(b.f("ix_auto_approve_configs_document_type"))
    op.drop_table("auto_approve_configs")
```

- [ ] **Step 3: Schemas.** In `backend/app/schemas.py`, add (near `WebhookCreate`):

```python
class AutoApproveCreate(BaseModel):
    document_type: str = Field(pattern=r"^[a-z0-9_-]+$")
    enabled: bool = False
    min_confidence: float = Field(gt=0.9, le=1.0)

class AutoApproveUpdate(BaseModel):
    enabled: bool | None = None
    min_confidence: float | None = Field(default=None, gt=0.9, le=1.0)

class AutoApproveOut(BaseModel):
    id: int
    document_type: str
    enabled: bool
    min_confidence: float
    created_at: datetime | None = None
```

(`datetime` is already imported in `schemas.py` from the A3 work.)

- [ ] **Step 4: Write the failing test.** Create `backend/tests/test_a4_autoapprove.py`:

```python
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

BACKEND = Path(__file__).resolve().parents[1]


def test_migration_0007_creates_table(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    assert "auto_approve_configs" in inspect(create_engine(url)).get_table_names()
```

- [ ] **Step 5: Run + full suite.** Run (from `backend/`): `python3 -m pytest tests/test_a4_autoapprove.py -q` → PASS; `python3 -m pytest -q` → all green (211).

- [ ] **Step 6: Commit.**

```bash
git add backend/app/models.py backend/alembic/versions/0007_auto_approve_configs.py backend/app/schemas.py backend/tests/test_a4_autoapprove.py
git commit -m "feat: AutoApproveConfig model + migration 0007 + schemas"
```

---

### Task 2: `Document.webhook_status`/`webhook_detail`/`auto_approved` + migration `0008`

**Files:**
- Modify: `backend/app/models.py` (Document)
- Create: `backend/alembic/versions/0008_document_delivery_flags.py`
- Test: `backend/tests/test_a4_autoapprove.py` (append)

**Interfaces:**
- Produces: `Document.webhook_status: str | None`, `Document.webhook_detail: str | None`, `Document.auto_approved: bool`.

- [ ] **Step 1: Extend the model.** In `backend/app/models.py` `class Document`, after `revision`:

```python
    webhook_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    webhook_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_approved: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
```

- [ ] **Step 2: Migration.** Create `backend/alembic/versions/0008_document_delivery_flags.py`:

```python
"""document webhook delivery status + auto_approved flag

Revision ID: 0008_document_delivery_flags
Revises: 0007_auto_approve_configs
Create Date: 2026-08-16 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0008_document_delivery_flags"
down_revision: Union[str, None] = "0007_auto_approve_configs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("documents", schema=None) as b:
        b.add_column(sa.Column("webhook_status", sa.String(length=20), nullable=True))
        b.add_column(sa.Column("webhook_detail", sa.Text(), nullable=True))
        b.add_column(sa.Column("auto_approved", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("documents", schema=None) as b:
        b.drop_column("auto_approved")
        b.drop_column("webhook_detail")
        b.drop_column("webhook_status")
```

- [ ] **Step 3: Write the failing test.** Append to `backend/tests/test_a4_autoapprove.py`:

```python
def test_migration_0008_adds_document_columns(tmp_path):
    url = f"sqlite:///{tmp_path / 'm2.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    cols = {c["name"] for c in inspect(create_engine(url)).get_columns("documents")}
    assert {"webhook_status", "webhook_detail", "auto_approved"} <= cols
```

- [ ] **Step 4: Run + full suite.** `python3 -m pytest tests/test_a4_autoapprove.py -q` → PASS; `python3 -m pytest -q` → green (212).

- [ ] **Step 5: Commit.**

```bash
git add backend/app/models.py backend/alembic/versions/0008_document_delivery_flags.py backend/tests/test_a4_autoapprove.py
git commit -m "feat: Document webhook_status/webhook_detail/auto_approved + migration 0008"
```

---

### Task 3: `services.apply_approval` + refactor the human approve path

**Files:**
- Modify: `backend/app/services.py` (add `apply_approval`)
- Modify: `backend/app/main.py` (`update_document` approve branch)
- Test: `backend/tests/test_a4_autoapprove.py` (append)

**Interfaces:**
- Produces: `services.apply_approval(db, document, prior_status, actor, action_label="Approved", details="") -> None` (raises `ValueError` on illegal transition).

- [ ] **Step 1: Write the failing tests.** Append to `backend/tests/test_a4_autoapprove.py`:

```python
import pytest
from app import services
from app.models import Document


def _doc(db, status="review_required"):
    d = Document(filename="a.pdf", document_type="invoice", stored_path="p",
                 status=status, review_required=(status == "review_required"), confidence=0.95)
    db.add(d); db.commit(); db.refresh(d)
    return d


def test_apply_approval_sets_state_and_audits(db_session):
    d = _doc(db_session)
    services.apply_approval(db_session, d, prior_status="review_required", actor=None,
                            action_label="Auto-approved", details="conf 0.95")
    db_session.commit(); db_session.refresh(d)
    assert d.status == "approved" and d.review_required is False and d.revision == 1
    from app.models import AuditLog
    assert db_session.query(AuditLog).filter_by(document_id=d.id, action="Auto-approved").count() == 1


def test_apply_approval_rejects_illegal_transition(db_session):
    d = _doc(db_session, status="uploaded")
    with pytest.raises(ValueError):
        services.apply_approval(db_session, d, prior_status="uploaded", actor=None)


def test_human_approve_still_works(client, db_session):
    d = _doc(db_session)
    r = client.put(f"/document/{d.id}", json={"fields": [], "action": "approve"})
    assert r.status_code == 200
    db_session.refresh(d)
    assert d.status == "approved" and d.revision == 1
```

- [ ] **Step 2: Run to verify fail.** `python3 -m pytest tests/test_a4_autoapprove.py -k "apply_approval or human_approve" -q` → FAIL (no `apply_approval`).

- [ ] **Step 3: Implement `apply_approval`.** In `backend/app/services.py`, add:

```python
def apply_approval(db: Session, document, prior_status: str, actor,
                   action_label: str = "Approved", details: str = "") -> None:
    """The single 'become approved' state change (status + revision + audit),
    used by both the human PUT /document path and the pipeline auto-approve path."""
    if not can_transition(prior_status, "approved"):
        raise ValueError(f"Cannot move a document from '{prior_status}' to 'approved'")
    document.status = "approved"
    document.review_required = False
    document.revision += 1
    log(db, document.id, action_label, details, actor=actor)
```

- [ ] **Step 4: Refactor the human approve branch.** In `backend/app/main.py` `update_document`, replace the tail (from `if outcome["status"] is not None:` through the `return serialize(doc)`) with:

```python
    if outcome["status"] == "approved":
        try:
            apply_approval(db, doc, prior_status, actor=user,
                           action_label=outcome["log_action"], details=outcome["log_details"])
        except ValueError as e:
            raise HTTPException(409, str(e))
    else:
        if outcome["status"] is not None:
            if not can_transition(prior_status, outcome["status"]):
                raise HTTPException(409, f"Cannot move a document from '{prior_status}' to '{outcome['status']}'")
            doc.status = outcome["status"]
        if outcome["review_required"] is not None:
            doc.review_required = outcome["review_required"]
        log(db, doc.id, outcome["log_action"], outcome["log_details"], actor=user)
    db.commit(); db.refresh(doc)
    if outcome["status"] == "approved":
        cfg = db.query(WebhookConfig).filter_by(document_type=doc.document_type, active=True).first()
        if cfg:
            doc.webhook_status = "pending"; db.commit()
            background_tasks.add_task(deliver_webhook, cfg.id, doc.id, user.email)
    return serialize(doc)
```

Add `apply_approval` to the `.services` import in `main.py`.

- [ ] **Step 5: Run tests + full suite.** `python3 -m pytest -q` → green (215). Existing approve/reject/reopen tests (`test_b11_reopen.py`, `test_phase1_rbac.py`) still pass (behavior-preserving refactor).

- [ ] **Step 6: Commit.**

```bash
git add backend/app/services.py backend/app/main.py backend/tests/test_a4_autoapprove.py
git commit -m "feat: services.apply_approval + refactor human approve path onto it"
```

---

### Task 4: `services.should_auto_approve` + `AUTO_APPROVE_ENABLED`

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/services.py`
- Test: `backend/tests/test_a4_autoapprove.py` (append)

**Interfaces:**
- Produces: `config.AUTO_APPROVE_ENABLED: bool`; `services.should_auto_approve(db, document) -> bool`.

- [ ] **Step 1: Add the flag.** In `backend/app/config.py`, after `SCHEMA_AUTHOR_ENABLED`:

```python
AUTO_APPROVE_ENABLED = os.getenv("AUTO_APPROVE_ENABLED", "false").lower() in ("1", "true", "yes")
```

- [ ] **Step 2: Write the failing tests.** Append to `backend/tests/test_a4_autoapprove.py`:

```python
from app.models import AutoApproveConfig
from app import config as appconfig


def _cfg(db, dt="invoice", enabled=True, floor=0.95):
    c = AutoApproveConfig(document_type=dt, enabled=enabled, min_confidence=floor)
    db.add(c); db.commit()


def test_should_auto_approve_true_when_all_conditions(db_session, monkeypatch):
    monkeypatch.setattr(appconfig, "AUTO_APPROVE_ENABLED", True)
    _cfg(db_session)
    d = _doc(db_session); d.review_required = False; d.confidence = 0.96; db_session.commit()
    assert services.should_auto_approve(db_session, d) is True


def test_should_auto_approve_false_paths(db_session, monkeypatch):
    _cfg(db_session)
    d = _doc(db_session); d.review_required = False; d.confidence = 0.96; db_session.commit()
    monkeypatch.setattr(appconfig, "AUTO_APPROVE_ENABLED", False)
    assert services.should_auto_approve(db_session, d) is False          # global off
    monkeypatch.setattr(appconfig, "AUTO_APPROVE_ENABLED", True)
    d.confidence = 0.80; db_session.commit()
    assert services.should_auto_approve(db_session, d) is False          # below floor
    d.confidence = 0.96; d.review_required = True; db_session.commit()
    assert services.should_auto_approve(db_session, d) is False          # review_required


def test_should_auto_approve_false_when_no_config(db_session, monkeypatch):
    monkeypatch.setattr(appconfig, "AUTO_APPROVE_ENABLED", True)
    d = _doc(db_session); d.review_required = False; d.confidence = 0.99; db_session.commit()
    assert services.should_auto_approve(db_session, d) is False          # no AutoApproveConfig row
```

- [ ] **Step 3: Implement.** In `backend/app/services.py` (import `AutoApproveConfig` from `.models`; `config` is already imported), add:

```python
def should_auto_approve(db: Session, document) -> bool:
    """True iff global flag on AND this type opted-in AND the doc cleared review
    AND its (already-computed) confidence meets the per-type floor. Reads the
    pipeline's committed confidence/review_required — never recomputes them."""
    if not config.AUTO_APPROVE_ENABLED:
        return False
    cfg = db.query(AutoApproveConfig).filter_by(document_type=document.document_type).first()
    if not cfg or not cfg.enabled:
        return False
    return (not document.review_required
            and document.confidence is not None
            and document.confidence >= cfg.min_confidence)
```

- [ ] **Step 4: Run + full suite.** `python3 -m pytest -q` → green (219).

- [ ] **Step 5: Commit.**

```bash
git add backend/app/config.py backend/app/services.py backend/tests/test_a4_autoapprove.py
git commit -m "feat: should_auto_approve gate + AUTO_APPROVE_ENABLED flag"
```

---

### Task 5: Pipeline auto-approve integration

**Files:**
- Modify: `backend/app/agents/pipeline.py`
- Test: `backend/tests/test_a4_autoapprove.py` (append)

**Interfaces:**
- Consumes: `services.should_auto_approve`, `services.apply_approval`, `webhooks.deliver_webhook`, `AutoApproveConfig`, `WebhookConfig`.

- [ ] **Step 1: Write the failing tests.** Append to `backend/tests/test_a4_autoapprove.py`. These drive `run_pipeline` with the classifier/extractor stubbed so no LLM/network runs — mirror the existing pipeline-test pattern in `backend/tests/` (inspect `test_phase2*`/`test_phase4f4_classifier.py` for the stubbing helpers) and assert:
  - eligible doc (global on, type enabled, confidence ≥ floor, not review_required) → `status=="approved"`, `auto_approved is True`, an `"Auto-approved"` audit with null actor, `revision==1`;
  - a configured active webhook → `deliver_webhook` called with `approved_by="system:auto-approve"` (monkeypatch `pipeline.deliver_webhook` to a spy) and `webhook_status` set;
  - global off / type disabled / below floor / review_required → stays `processed`/`review_required`, `auto_approved is False`, spy not called.

(Write the concrete cases using the repo's existing pipeline-invocation test scaffolding; if that scaffolding is heavy, factor the finalize decision so the auto-approve branch can be tested via a small helper. Keep it offline.)

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Implement.** In `backend/app/agents/pipeline.py`, after the existing finalize block sets `document.status`/`review_required`/`confidence` and logs `"Processed"`, and BEFORE the final `db.commit(); db.refresh(document); return document`, insert:

```python
        auto = services.should_auto_approve(db, document)
        cfg_w = None
        if auto:
            services.apply_approval(db, document, prior_status=document.status, actor=None,
                                    action_label="Auto-approved",
                                    details=f"confidence {document.confidence:.2f} >= floor; validator-clean, no anomalies")
            document.auto_approved = True
            cfg_w = db.query(WebhookConfig).filter_by(document_type=document.document_type, active=True).first()
            if cfg_w:
                document.webhook_status = "pending"
        db.commit(); db.refresh(document)
        if auto and cfg_w:
            deliver_webhook(cfg_w.id, document.id, "system:auto-approve")
        return document
```

Import at the top of `pipeline.py`: `from ..models import WebhookConfig` (already imports `Document, ExtractedField`) and `from ..webhooks import deliver_webhook`. `services` is already imported. Remove the old trailing `db.commit(); db.refresh(document); return document` that this replaces (don't double-commit/return).

- [ ] **Step 4: Run tests + full suite.** `python3 -m pytest -q` → green. Existing pipeline tests still pass (auto defaults off → no behavior change on the default path).

- [ ] **Step 5: Commit.**

```bash
git add backend/app/agents/pipeline.py backend/tests/test_a4_autoapprove.py
git commit -m "feat: pipeline auto-approves eligible documents + pushes webhook (system actor)"
```

---

### Task 6: `deliver_webhook` retry + delivery status

**Files:**
- Modify: `backend/app/webhooks.py`
- Test: `backend/tests/test_a4_autoapprove.py` (append)

**Interfaces:**
- `deliver_webhook` now also sets `doc.webhook_status` (`"delivered"`/`"failed"`) + `doc.webhook_detail`, retrying transient failures.

- [ ] **Step 1: Write the failing tests.** Append (monkeypatch `webhooks.requests.post`, `webhooks.SessionLocal` → the test session, as in `test_phase3s3_webhooks.py`):
  - two failing posts then a 200 → `webhook_status=="delivered"`, `requests.post` called 3×;
  - all attempts fail → `webhook_status=="failed"`, `webhook_detail` set, never raises;
  - a 200 first try → delivered, called once.

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Implement.** In `backend/app/webhooks.py` `deliver_webhook`, wrap the POST in a small retry loop (e.g. up to 3 attempts with a short backoff — use a module-level `_RETRIES = 3` and `time.sleep(0.2 * attempt)`; keep the existing audit logging), and on the terminal outcome set `doc.webhook_status`/`doc.webhook_detail` before `db.commit()`. On 2xx → `doc.webhook_status = "delivered"`, `doc.webhook_detail = f"{cfg.url} ({resp.status_code})"`, stop. On non-2xx/exception → record and retry; after the last attempt → `doc.webhook_status = "failed"`, `doc.webhook_detail = <reason>`. Keep the outer try/except so it never raises; keep `db.close()` in `finally`.

(Import `time` if not already imported. Tests must not sleep meaningfully — keep backoff tiny, or make the base delay a module constant the test can monkeypatch to 0.)

- [ ] **Step 4: Run tests + full suite.** `python3 -m pytest -q` → green. Existing `test_phase3s3_webhooks.py` delivery tests still pass (single-attempt success path unchanged; failures now retried — update those tests only if they assert an exact call count, and note it).

- [ ] **Step 5: Commit.**

```bash
git add backend/app/webhooks.py backend/tests/test_a4_autoapprove.py
git commit -m "feat: deliver_webhook retry-with-backoff + records Document.webhook_status"
```

---

### Task 7: `AutoApproveConfig` admin CRUD + per-type eval endpoint

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_a4_autoapprove.py` (append)

**Interfaces:**
- Produces: `GET/POST/PATCH/DELETE /auto-approve`; `GET /auto-approve/eval/{document_type}`.

- [ ] **Step 1: Write the failing tests.** Append: CRUD (create 201; duplicate type 409; `min_confidence=0.9` or `<=0.9` → 422; patch enable + floor; delete 204/404); admin-only (reviewer → 403 on all); enabling (`enabled` false→true via PATCH) writes an audit-ish log entry; `GET /auto-approve/eval/{type}` returns the `build_report` shape and is admin-only.

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Implement.** In `backend/app/main.py` (import `AutoApproveConfig`, `AutoApproveCreate/Update/Out`, and `from . import eval as eval_mod`), add endpoints modeled on the `/webhooks` CRUD:
  - `GET /auto-approve` (admin) → list `AutoApproveOut`.
  - `POST /auto-approve` (201, admin) → 409 if the `document_type` already has a row; else create. (`min_confidence > 0.9` enforced by the Pydantic schema → 422.)
  - `PATCH /auto-approve/{id}` (admin) → 404 if missing; apply `model_dump(exclude_unset=True)`; if this flips `enabled` false→true, `log(db, <a doc id? no> ...)` — since `AuditLog.document_id` is a non-nullable FK, record the enable event via the app logger (`logging.getLogger("aethermind").info(...)`) OR a dedicated lightweight table; **for this slice use the app logger** (an AuditLog row needs a document_id it doesn't have). Return `AutoApproveOut`.
  - `DELETE /auto-approve/{id}` (204, admin) → 404 if missing.
  - `GET /auto-approve/eval/{document_type}` (admin) → `eval_mod.build_report(eval_mod.correction_records(db, document_type))`.

  Note on the enable-audit: `AuditLog` is document-scoped (non-nullable `document_id`), so a config-level enable can't be an `AuditLog` row without a document. Log it via the application logger for now; a proper config-audit table is a fast-follow (out of scope). Call this out in the report.

- [ ] **Step 4: Run tests + full suite.** `python3 -m pytest -q` → green.

- [ ] **Step 5: Commit.**

```bash
git add backend/app/main.py backend/tests/test_a4_autoapprove.py
git commit -m "feat: /auto-approve admin CRUD (floor>0.9) + /auto-approve/eval/{type}"
```

---

### Task 8: `/documents/stats` extensions

**Files:**
- Modify: `backend/app/main.py` (`documents_stats`)
- Modify: `backend/app/main.py` (`serialize_summary` — add `auto_approved`)
- Test: `backend/tests/test_a4_autoapprove.py` (append)

**Interfaces:**
- `GET /documents/stats` gains `auto_approved`, `auto_approved_reopen_rate`, `webhook_failed`; `serialize_summary` gains `auto_approved`.

- [ ] **Step 1: Write the failing tests.** Append: seed docs with `auto_approved=True` (some now `reopened`), some `webhook_status="failed"` → assert `auto_approved` count, `auto_approved_reopen_rate` (reopened-and-auto ÷ auto, or `None` when no auto docs), `webhook_failed` count; and that `GET /documents` list items include `auto_approved`.

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Implement.** In `documents_stats`, add:
```python
    auto = db.query(Document).filter(Document.auto_approved.is_(True)).count()
    auto_reopened = db.query(Document).filter(Document.auto_approved.is_(True),
                                              Document.status == "reopened").count()
    webhook_failed = db.query(Document).filter(Document.webhook_status == "failed").count()
    # ... add to the returned dict:
    #   "auto_approved": auto,
    #   "auto_approved_reopen_rate": round(auto_reopened / auto, 2) if auto else None,
    #   "webhook_failed": webhook_failed,
```
Add `"auto_approved": d.auto_approved` to `serialize_summary`.

- [ ] **Step 4: Run tests + full suite.** `python3 -m pytest -q` → green.

- [ ] **Step 5: Commit.**

```bash
git add backend/app/main.py backend/tests/test_a4_autoapprove.py
git commit -m "feat: /documents/stats auto_approved + reopen-rate + webhook_failed; serialize_summary auto_approved"
```

---

### Task 9: Frontend — Auto-approve admin screen + nav link

**Files:**
- Create: `frontend/app/auto-approve/page.tsx`
- Modify: `frontend/app/page.tsx` (admin nav link)
- Test: `frontend/app/auto-approve.test.tsx` (new)

- [ ] **Step 1: Implement the screen.** `me()`-guarded admin, mirroring `frontend/app/webhooks/page.tsx`. Lists document types with an `enabled` toggle + `min_confidence` input (Save → `POST`/`PATCH /auto-approve`). For each type, fetch `GET /auto-approve/eval/{type}` and render the safety snapshot: `grounded_but_wrong.rate`, `n`, and the report `header` (the "upper bound" text). **Disable the enable toggle when the report's total `n` is below the min-N threshold** (show "insufficient data — do not enable"). Render a read-only global-kill-switch banner (from a small field on `/auto-approve` list response or a dedicated `GET /auto-approve/status` — simplest: include `global_enabled` in the list response). Show the static downstream-push + self-blinding warnings. Reuse `ConfirmDialog` for enabling (an acknowledgement step).

- [ ] **Step 2: Nav link.** In `frontend/app/page.tsx` (the admin links group at ~line 75, after "Suggested Schemas"), add `{role&&isAdmin(role)&&<a href="/auto-approve" className="font-semibold text-slate-700 hover:underline">Auto-approve</a>}`.

- [ ] **Step 3: Test.** Create `frontend/app/auto-approve.test.tsx` (`// @vitest-environment jsdom`): mock `api`/`me`; assert the screen lists a type with its eval snapshot, and that the toggle is disabled when `n` is insufficient.

- [ ] **Step 4: Run tests + build.** From `frontend/`: `npx vitest run && npm run build` → green (37).

- [ ] **Step 5: Commit.**

```bash
git add frontend/app/auto-approve/page.tsx frontend/app/page.tsx frontend/app/auto-approve.test.tsx
git commit -m "feat: admin Auto-approve screen (eval-gated enable) + nav link"
```

---

### Task 10: Frontend — queue auto-approved indicator/filter + dashboard tiles

**Files:**
- Modify: `frontend/app/page.tsx`
- Test: `frontend/app/auto-approve-surfacing.test.tsx` (new)

- [ ] **Step 1: Implement.** In `frontend/app/page.tsx`:
  - Add `auto_approved?: boolean` to the `Doc` type; render a small "⚡ auto" indicator next to the status badge when `d.auto_approved`.
  - Add an "Auto-approved only" checkbox near the search/status-filter row that client-side filters the rendered `docs` to `auto_approved` ones.
  - Extend the `stats` state type with `auto_approved:number`, `auto_approved_reopen_rate:number|null`, `webhook_failed:number`, and add three tiles to the dashboard array: `["Auto-approved", stats?.auto_approved??0, Bot]`, `["Auto-reopen rate", agreement-style % or "—", ...]`, `["Webhook failures", stats?.webhook_failed??0, TriangleAlert]` (reuse already-imported icons).

- [ ] **Step 2: Test.** Create `frontend/app/auto-approve-surfacing.test.tsx`: mock stats + a doc with `auto_approved:true`; assert the "auto" indicator renders and the tiles show the values.

- [ ] **Step 3: Run tests + build.** From `frontend/`: `npx vitest run && npm run build` → green (38).

- [ ] **Step 4: Commit.**

```bash
git add frontend/app/page.tsx frontend/app/auto-approve-surfacing.test.tsx
git commit -m "feat: queue auto-approved indicator/filter + auto-approve/webhook dashboard tiles"
```

---

### Task 11: Docs

**Files:**
- Modify: `.env.example`, `docs/deployment.md`, `docs/roadmap.md`

- [ ] **Step 1: `.env.example`.** Add `AUTO_APPROVE_ENABLED=false` with a one-line comment (near `SCHEMA_AUTHOR_ENABLED`).

- [ ] **Step 2: `docs/deployment.md`.** Add an `## A4 / B9 — auto-approve (straight-through processing)` section: what it does; the belt-and-suspenders gate; migrations `0007`/`0008`; `AUTO_APPROVE_ENABLED` (default false); the per-type admin screen; webhook retry + delivery-status; and the **operational prerequisites before enabling any type in prod** — curate a golden set, verify grounded-but-wrong at the chosen floor via `scripts/eval.py`, set `min_confidence` well above 0.9, keep golden-set curation running (self-blinding loop), and use B11 reopen to remediate.

- [ ] **Step 3: `docs/roadmap.md`.** Mark **A4 ✅ Shipped** and set next to **A5 (observability)**.

- [ ] **Step 4: Commit.**

```bash
git add .env.example docs/deployment.md docs/roadmap.md
git commit -m "docs: A4/B9 auto-approve — env, deployment (enable prerequisites), roadmap"
```

---

## Self-Review

**Spec coverage:** `AutoApproveConfig` + migration 0007 + schemas → Task 1. `Document` delivery/auto flags + migration 0008 → Task 2. `apply_approval` (shared) + human-path refactor → Task 3. `should_auto_approve` + env flag → Task 4. Pipeline auto-approve + direct webhook + system actor → Task 5. `deliver_webhook` retry + `webhook_status` → Task 6. Config CRUD (floor>0.9) + per-type eval endpoint → Task 7. Stats extensions + `serialize_summary.auto_approved` → Task 8. Admin screen (eval-gated enable) → Task 9. Queue indicator/filter + tiles → Task 10. Docs (+ enable prerequisites) → Task 11. Belt-and-suspenders guardrails: global env (T4) + per-type opt-in (T1/T4) + floor>0.9 (T1/T7) + machine audit stamp + actor=None (T5) + reopen (already live) + eval-gated UI (T9).

**Placeholder scan:** No TBD/TODO. Tasks 5 and 6 point the implementer at existing pipeline/webhook test scaffolding rather than restating it (the concrete assertions are enumerated); that's a deliberate reuse instruction, not a placeholder. Note the one design compromise called out explicitly: the config-enable audit uses the app logger (not an `AuditLog` row) because `AuditLog.document_id` is non-nullable — flagged for the reviewer and as a fast-follow.

**Type consistency:** `apply_approval(db, document, prior_status, actor, action_label, details)` defined in T3, reused in T5. `should_auto_approve(db, document)` T4 → T5. `AutoApproveConfig`/`AutoApprove*` schemas T1 → T7. `Document.auto_approved`/`webhook_status` T2 → set in T5/T6 → surfaced in T8/T9/T10. Migrations chain `0006`→`0007`→`0008`. Test counts cumulative (210 → ~220+ backend; 36 → ~39 frontend). **Not executed — planning only.**
