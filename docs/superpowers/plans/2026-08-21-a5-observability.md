# A5 — Observability (lite) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add structured JSON logging and an admin System-Health view (ops-metrics endpoint) — computed from existing data, no new infrastructure.

**Architecture:** A stdlib `JsonFormatter` + `configure_logging()` (no new dep) installed at startup; correlation `extra` fields at high-value log points; an admin-only `GET /admin/metrics` returning status counts, recent errors, stuck-processing, and per-stage latency (from the stored `pipeline_trace`); an admin System Health screen.

**Tech Stack:** FastAPI + SQLAlchemy (backend), Next.js 15 / React 19 / TS / Tailwind (frontend), pytest, vitest + @testing-library/react (jsdom).

**Spec:** `docs/superpowers/specs/2026-08-21-a5-observability-design.md`

## Global Constraints

- **No new dependencies** (stdlib `json`/`logging` only), no migration, no schema change.
- `/admin/metrics` is **admin-only** (`Depends(auth.require_role("admin"))`); `/health` and `/documents/stats` are unchanged.
- All computed from existing data (`Document.status`, `AuditLog`, `pipeline_trace`, `upload_date`). Per-doc `pipeline_trace` parsing is guarded (a malformed trace is skipped, never 500s the endpoint).
- Config additions defaulted: `LOG_FORMAT=json`, `STUCK_PROCESSING_MINUTES=15`, `METRICS_RECENT_N=200`.
- `configure_logging()` is idempotent and never raises at startup.
- Alerting is **visual flags only** (no external email/Slack).
- Keep backend **241** / frontend **39** tests green (net new on top).
- Match existing code style; frontend admin screens are `me()`-guarded and mirror the existing ones.

---

### Task 1: Structured JSON logging (`logging_config.py`) + config + startup wiring

**Files:**
- Create: `backend/app/logging_config.py`
- Modify: `backend/app/config.py` (3 flags), `backend/app/main.py` (import + call `configure_logging()`)
- Test: `backend/tests/test_a5_observability.py` (new)

**Interfaces:**
- Produces: `logging_config.JsonFormatter` (a `logging.Formatter`), `logging_config.configure_logging() -> None`; `config.LOG_FORMAT`, `config.STUCK_PROCESSING_MINUTES`, `config.METRICS_RECENT_N`.

- [ ] **Step 1: Add config flags.** In `backend/app/config.py`, after the `AUTO_APPROVE_ENABLED` line, add:

```python
LOG_FORMAT = os.getenv("LOG_FORMAT", "json")
STUCK_PROCESSING_MINUTES = int(os.getenv("STUCK_PROCESSING_MINUTES", "15"))
METRICS_RECENT_N = int(os.getenv("METRICS_RECENT_N", "200"))
```

- [ ] **Step 2: Write the failing tests.** Create `backend/tests/test_a5_observability.py`:

```python
import json
import logging
import sys
from app.logging_config import JsonFormatter, configure_logging
from app import config


def test_json_formatter_basic():
    rec = logging.LogRecord("aethermind", logging.INFO, __file__, 1, "hello", None, None)
    out = json.loads(JsonFormatter().format(rec))
    assert out["level"] == "INFO" and out["logger"] == "aethermind"
    assert out["msg"] == "hello" and "ts" in out


def test_json_formatter_extra_and_exc():
    rec = logging.LogRecord("aethermind", logging.INFO, __file__, 1, "m", None, None)
    rec.document_id = 7
    rec.stage = "pipeline"
    out = json.loads(JsonFormatter().format(rec))
    assert out["document_id"] == 7 and out["stage"] == "pipeline"
    try:
        raise ValueError("boom")
    except ValueError:
        rec2 = logging.LogRecord("aethermind", logging.ERROR, __file__, 1, "err", None, sys.exc_info())
    out2 = json.loads(JsonFormatter().format(rec2))
    assert "boom" in out2["exc"]


def test_configure_logging_idempotent(monkeypatch):
    monkeypatch.setattr(config, "LOG_FORMAT", "json")
    configure_logging()
    configure_logging()
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_aethermind", False)]
    assert len(ours) == 1


def test_configure_logging_plain(monkeypatch):
    monkeypatch.setattr(config, "LOG_FORMAT", "plain")
    configure_logging()
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_aethermind", False)]
    assert len(ours) == 1 and not isinstance(ours[0].formatter, JsonFormatter)
```

- [ ] **Step 3: Run to verify fail.** Run (from `backend/`): `python3 -m pytest tests/test_a5_observability.py -q`
Expected: FAIL — `app.logging_config` doesn't exist.

- [ ] **Step 4: Implement `backend/app/logging_config.py`:**

```python
import json
import logging
from datetime import datetime, timezone
from . import config

_EXTRA_KEYS = ("document_id", "stage", "latency_ms", "actor", "status")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in _EXTRA_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Install a single stdlib handler on the root logger, JSON or plain per
    config.LOG_FORMAT. Idempotent (removes any handler we previously added);
    never raises at startup."""
    try:
        root = logging.getLogger()
        for handler in [h for h in root.handlers if getattr(h, "_aethermind", False)]:
            root.removeHandler(handler)
        handler = logging.StreamHandler()
        handler._aethermind = True
        if config.LOG_FORMAT == "json":
            handler.setFormatter(JsonFormatter())
        else:
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        logging.getLogger("aethermind").setLevel(logging.INFO)
    except Exception:
        pass
```

- [ ] **Step 5: Wire into startup.** In `backend/app/main.py`, add to the local imports (after `from . import auth, config, mcp_server`):
```python
from .logging_config import configure_logging
```
and call it once immediately before `app = FastAPI(...)` (line ~26):
```python
configure_logging()
app = FastAPI(title="Document Intelligence Engine", version="1.0.0")
```

- [ ] **Step 6: Run tests + full suite.** Run (from `backend/`): `python3 -m pytest tests/test_a5_observability.py -q` → PASS; then `python3 -m pytest -q` → all green (245 = 241 + 4 new).

- [ ] **Step 7: Commit.**

```bash
git add backend/app/logging_config.py backend/app/config.py backend/app/main.py backend/tests/test_a5_observability.py
git commit -m "feat: structured JSON logging (configure_logging + JsonFormatter) + config flags"
```

---

### Task 2: Correlation fields at high-value log points

**Files:**
- Modify: `backend/app/agents/pipeline.py`
- Test: `backend/tests/test_a5_observability.py` (append)

**Interfaces:**
- Consumes: the `"aethermind"` logger + `JsonFormatter` `_EXTRA_KEYS` allow-list (Task 1).

- [ ] **Step 1: Write the failing test.** Append to `backend/tests/test_a5_observability.py`:

```python
import logging as _logging
from app.agents import pipeline
from app.models import Document, AutoApproveConfig
from app import config as _config, services


class _Capture(_logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_auto_approve_emits_structured_log(db_session, monkeypatch):
    monkeypatch.setattr(_config, "AUTO_APPROVE_ENABLED", True)
    doc = Document(filename="a.pdf", document_type="invoice", stored_path="p",
                   status="processed", review_required=False, confidence=0.97)
    db_session.add(doc)
    db_session.add(AutoApproveConfig(document_type="invoice", enabled=True, min_confidence=0.95))
    db_session.commit(); db_session.refresh(doc)
    monkeypatch.setattr(pipeline, "SessionLocal", lambda: db_session, raising=False)
    cap = _Capture()
    lg = _logging.getLogger("aethermind"); lg.addHandler(cap)
    try:
        pipeline._maybe_auto_approve(db_session, doc)
    finally:
        lg.removeHandler(cap)
    hit = [r for r in cap.records if getattr(r, "document_id", None) == doc.id
           and "auto" in r.getMessage().lower()]
    assert hit, "expected a structured auto-approve log record carrying document_id"
```

- [ ] **Step 2: Run to verify fail.** Run (from `backend/`): `python3 -m pytest tests/test_a5_observability.py -k auto_approve_emits -q`
Expected: FAIL — no such log record.

- [ ] **Step 3: Implement.** In `backend/app/agents/pipeline.py`:
- Add a module logger after the imports (near the top, after line ~10):
```python
import logging
_log = logging.getLogger("aethermind")
```
- In `_maybe_auto_approve`, right after `services.apply_approval(...)` succeeds (before or after setting `auto_approved`), add:
```python
        _log.info("document auto-approved", extra={"document_id": document.id,
                                                   "actor": "system:auto-approve"})
```
- In `run_pipeline`, just before the finalize `return document` (after `processing_time` is set), add:
```python
        _log.info("pipeline complete", extra={"document_id": document.id, "stage": "pipeline",
                                              "latency_ms": int((document.processing_time or 0) * 1000),
                                              "status": document.status})
```
- In the `except Exception as exc:` finalize-error branch (which already sets status="error" and logs an audit), add a structured app-log alongside it:
```python
        _log.error("pipeline failed", extra={"document_id": document.id, "stage": "pipeline"}, exc_info=True)
```

- [ ] **Step 4: Run tests + full suite.** Run (from `backend/`): `python3 -m pytest tests/test_a5_observability.py -q` → PASS; `python3 -m pytest -q` → green (246 = 245 + 1). Existing pipeline/auto-approve tests unaffected (added logs are side-effect-free).

- [ ] **Step 5: Commit.**

```bash
git add backend/app/agents/pipeline.py backend/tests/test_a5_observability.py
git commit -m "feat: structured correlation logs at pipeline + auto-approve points"
```

---

### Task 3: `GET /admin/metrics` + `_percentile`

**Files:**
- Modify: `backend/app/main.py` (add `_percentile`, `GET /admin/metrics`; add `timedelta` to the datetime import)
- Test: `backend/tests/test_a5_observability.py` (append)

**Interfaces:**
- Produces: `GET /admin/metrics` → `{status_counts, errors_recent, stuck_processing, stage_latency, sample_size}`; `_percentile(values, p)`.

- [ ] **Step 1: Write the failing tests.** Append to `backend/tests/test_a5_observability.py`:

```python
from datetime import datetime, timezone, timedelta
from app.models import AuditLog


def test_percentile():
    from app.main import _percentile
    assert _percentile([], 50) is None
    assert _percentile([10], 95) == 10
    assert _percentile([10, 20, 30, 40], 50) == 20      # nearest-rank
    assert _percentile([10, 20, 30, 40], 95) == 40


def test_admin_metrics_shape_and_admin_only(client, db_session):
    # seed a mix of statuses
    for st in ["processed", "approved", "error", "review_required"]:
        db_session.add(Document(filename=f"{st}.pdf", document_type="invoice", stored_path="p", status=st))
    db_session.commit()
    err = db_session.query(Document).filter_by(status="error").first()
    db_session.add(AuditLog(document_id=err.id, action="Processing failed", details="boom"))
    # a stuck processing doc (old upload_date) + a fresh one
    old = Document(filename="stuck.pdf", document_type="invoice", stored_path="p", status="processing",
                   upload_date=datetime.now(timezone.utc) - timedelta(hours=2))
    fresh = Document(filename="fresh.pdf", document_type="invoice", stored_path="p", status="processing",
                     upload_date=datetime.now(timezone.utc))
    # a doc with a pipeline_trace for latency
    traced = Document(filename="t.pdf", document_type="invoice", stored_path="p", status="processed",
                      pipeline_trace=[{"name": "Classifier", "status": "ok", "detail": "", "duration_ms": 100},
                                      {"name": "Extractor", "status": "ok", "detail": "", "duration_ms": 300}])
    db_session.add_all([old, fresh, traced]); db_session.commit()

    r = client.get("/admin/metrics")
    assert r.status_code == 200
    body = r.json()
    assert body["status_counts"]["error"] == 1 and body["status_counts"]["processing"] == 2
    assert any(e["detail"] == "boom" for e in body["errors_recent"])
    assert body["stuck_processing"]["count"] == 1                       # only the 2h-old one
    stages = {s["stage"]: s for s in body["stage_latency"]}
    assert stages["Classifier"]["p50_ms"] == 100 and stages["Extractor"]["n"] == 1


def test_admin_metrics_forbidden_for_reviewer(client, db_session, monkeypatch):
    from app import auth
    import app.main as main_mod
    rev = User(email="rev@t.local", password_hash="x", role="reviewer", is_active=True)
    db_session.add(rev); db_session.commit()
    main_mod.app.dependency_overrides[auth.get_current_user] = lambda: rev
    try:
        assert client.get("/admin/metrics").status_code == 403
    finally:
        main_mod.app.dependency_overrides.pop(auth.get_current_user, None)
```

(Add `from app.models import User` to the test file's imports if not already present.)

- [ ] **Step 2: Run to verify fail.** Run (from `backend/`): `python3 -m pytest tests/test_a5_observability.py -k "percentile or admin_metrics" -q`
Expected: FAIL — no `_percentile`, `/admin/metrics` 404.

- [ ] **Step 3: Implement.** In `backend/app/main.py`:
- Extend the datetime import (line 2) to include `timedelta`:
```python
from datetime import datetime, timezone, timedelta
```
- Add the helper + endpoint (near the other `/documents` handlers):
```python
import math

def _percentile(values: list[int], p: float):
    if not values:
        return None
    s = sorted(values)
    k = max(1, math.ceil(p / 100 * len(s)))
    return s[k - 1]


@app.get("/admin/metrics")
def admin_metrics(db: Session = Depends(get_db), _: User = Depends(auth.require_role("admin"))):
    statuses = ["uploaded", "processing", "processed", "review_required",
                "approved", "rejected", "reopened", "error"]
    status_counts = {s: db.query(Document).filter(Document.status == s).count() for s in statuses}

    err_docs = (db.query(Document).filter(Document.status == "error")
                .order_by(Document.upload_date.desc()).limit(10).all())
    errors_recent = []
    for d in err_docs:
        a = (db.query(AuditLog).filter(AuditLog.document_id == d.id, AuditLog.action == "Processing failed")
             .order_by(AuditLog.timestamp.desc()).first())
        errors_recent.append({"id": d.id, "filename": d.filename, "document_type": d.document_type,
                              "detail": a.details if a else None,
                              "timestamp": (a.timestamp if a else d.upload_date)})

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=config.STUCK_PROCESSING_MINUTES)
    stuck = []
    for d in db.query(Document).filter(Document.status == "processing").all():
        ud = d.upload_date
        if ud is not None and ud.tzinfo is None:
            ud = ud.replace(tzinfo=timezone.utc)
        if ud is not None and ud < cutoff:
            stuck.append({"id": d.id, "filename": d.filename,
                          "minutes": int((now - ud).total_seconds() // 60)})

    recent = (db.query(Document).filter(Document.pipeline_trace.isnot(None))
              .order_by(Document.upload_date.desc()).limit(config.METRICS_RECENT_N).all())
    by_stage: dict[str, list[int]] = {}
    for d in recent:
        try:
            for s in (d.pipeline_trace or []):
                if s.get("duration_ms") is not None:
                    by_stage.setdefault(s["name"], []).append(s["duration_ms"])
        except Exception:
            continue
    stage_latency = [{"stage": name, "p50_ms": _percentile(v, 50), "p95_ms": _percentile(v, 95), "n": len(v)}
                     for name, v in by_stage.items()]

    return {"status_counts": status_counts, "errors_recent": errors_recent,
            "stuck_processing": {"threshold_minutes": config.STUCK_PROCESSING_MINUTES,
                                 "count": len(stuck), "documents": stuck},
            "stage_latency": stage_latency, "sample_size": config.METRICS_RECENT_N}
```

- [ ] **Step 4: Run tests + full suite.** Run (from `backend/`): `python3 -m pytest tests/test_a5_observability.py -q` → PASS; `python3 -m pytest -q` → green (249 = 246 + 3).

- [ ] **Step 5: Commit.**

```bash
git add backend/app/main.py backend/tests/test_a5_observability.py
git commit -m "feat: admin /admin/metrics (status/errors/stuck/stage-latency) + _percentile"
```

---

### Task 4: Frontend — System Health screen + nav link

**Files:**
- Create: `frontend/app/health/page.tsx`
- Modify: `frontend/app/page.tsx` (nav link)
- Test: `frontend/app/health.test.tsx` (new)

**Interfaces:**
- Consumes: `GET /admin/metrics` (Task 3); `api`/`me` from `../../lib/api`.

- [ ] **Step 1: Write the failing test.** Create `frontend/app/health.test.tsx`:

```tsx
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import HealthPage from "./health/page";

vi.mock("../lib/api", () => ({
  me: vi.fn(async () => ({ role: "admin" })),
  api: vi.fn(async (path: string) => {
    if (path === "/admin/metrics") return {
      status_counts: { uploaded: 0, processing: 1, processed: 3, review_required: 1,
                       approved: 5, rejected: 0, reopened: 0, error: 2 },
      errors_recent: [{ id: 9, filename: "bad.pdf", document_type: "invoice", detail: "boom", timestamp: "2026-08-21T10:00:00" }],
      stuck_processing: { threshold_minutes: 15, count: 1, documents: [{ id: 4, filename: "stuck.pdf", minutes: 120 }] },
      stage_latency: [{ stage: "Classifier", p50_ms: 100, p95_ms: 180, n: 10 }],
      sample_size: 200,
    };
    return {};
  }),
}));

describe("System Health screen", () => {
  beforeEach(() => vi.clearAllMocks());
  it("renders metrics, stuck banner, errors, and stage latency", async () => {
    render(<HealthPage />);
    await waitFor(() => expect(screen.getByText(/stuck.pdf/)).toBeTruthy());
    expect(screen.getByText(/boom/)).toBeTruthy();
    expect(screen.getByText(/Classifier/)).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run to verify fail.** Run (from `frontend/`): `npx vitest run health`
Expected: FAIL — no `health/page.tsx`.

- [ ] **Step 3: Implement `frontend/app/health/page.tsx`** (mirror the `me()`-guard of `frontend/app/users/page.tsx`):

```tsx
"use client";
import { useEffect, useState } from "react";
import { api, me } from "../../lib/api";

type Metrics = {
  status_counts: Record<string, number>;
  errors_recent: { id: number; filename: string; document_type: string; detail: string | null; timestamp: string }[];
  stuck_processing: { threshold_minutes: number; count: number; documents: { id: number; filename: string; minutes: number }[] };
  stage_latency: { stage: string; p50_ms: number | null; p95_ms: number | null; n: number }[];
  sample_size: number;
};

export default function HealthPage() {
  const [m, setM] = useState<Metrics | null>(null);
  const [authorized, setAuthorized] = useState(false);
  const load = () => api("/admin/metrics").then(setM).catch(() => (window.location.href = "/login"));
  useEffect(() => {
    me().then((u: any) => {
      if (u.role !== "admin") { window.location.href = "/"; return; }
      setAuthorized(true); load();
    }).catch(() => (window.location.href = "/login"));
  }, []);
  if (!authorized || !m) return null;

  const stuck = m.stuck_processing.count;
  const errs = m.errors_recent.length;
  const banner = stuck > 0
    ? { cls: "bg-rose-50 text-rose-700", text: `${stuck} document(s) stuck in processing` }
    : errs > 0
    ? { cls: "bg-amber-50 text-amber-700", text: `${errs} recent processing error(s)` }
    : { cls: "bg-emerald-50 text-emerald-700", text: "All systems nominal" };

  return (
    <div className="mx-auto mt-10 max-w-3xl p-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold">System Health</h1>
        <button onClick={load} className="text-sm font-semibold text-slate-700">Refresh</button>
      </div>
      <div className={`mb-6 rounded-lg px-4 py-3 text-sm font-semibold ${banner.cls}`}>{banner.text}</div>

      <h2 className="mb-2 text-sm font-semibold text-slate-500">Documents by status</h2>
      <div className="mb-6 flex flex-wrap gap-2">
        {Object.entries(m.status_counts).map(([s, n]) => (
          <span key={s} className="rounded-full border border-slate-200 px-2.5 py-1 text-xs">
            {s.replaceAll("_", " ")}: <span className="font-semibold tabular-nums">{n}</span>
          </span>
        ))}
      </div>

      {stuck > 0 && (
        <div className="mb-6">
          <h2 className="mb-2 text-sm font-semibold text-slate-500">Stuck in processing</h2>
          <ul className="space-y-1 text-sm">
            {m.stuck_processing.documents.map((d) => (
              <li key={d.id}>{d.filename} — <span className="tabular-nums">{d.minutes}m</span></li>
            ))}
          </ul>
        </div>
      )}

      <h2 className="mb-2 text-sm font-semibold text-slate-500">Recent errors</h2>
      {m.errors_recent.length === 0 ? (
        <p className="mb-6 text-sm text-slate-500">None.</p>
      ) : (
        <ul className="mb-6 space-y-2 text-sm">
          {m.errors_recent.map((e) => (
            <li key={e.id} className="rounded border border-slate-200 p-2">
              <span className="font-medium">{e.filename}</span> <span className="text-xs text-slate-400">({e.document_type})</span>
              {e.detail && <p className="text-slate-500">{e.detail}</p>}
            </li>
          ))}
        </ul>
      )}

      <h2 className="mb-2 text-sm font-semibold text-slate-500">Pipeline stage latency <span className="font-normal text-slate-400">(last {m.sample_size})</span></h2>
      <div className="overflow-x-auto rounded border border-slate-200">
        <table className="w-full border-collapse text-sm">
          <thead><tr className="text-left text-xs uppercase tracking-wide text-slate-400">
            <th className="px-3 py-1.5">Stage</th><th className="px-3 py-1.5">p50 ms</th><th className="px-3 py-1.5">p95 ms</th><th className="px-3 py-1.5">n</th>
          </tr></thead>
          <tbody>
            {m.stage_latency.map((s) => (
              <tr key={s.stage}>
                <td className="border-t border-slate-100 px-3 py-1.5">{s.stage}</td>
                <td className="border-t border-slate-100 px-3 py-1.5 tabular-nums">{s.p50_ms ?? "—"}</td>
                <td className="border-t border-slate-100 px-3 py-1.5 tabular-nums">{s.p95_ms ?? "—"}</td>
                <td className="border-t border-slate-100 px-3 py-1.5 tabular-nums">{s.n}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

- [ ] **Step 2b: Add the nav link.** In `frontend/app/page.tsx`, immediately after the existing Auto-approve admin link (`href="/auto-approve"`), add a sibling with the same guard + className:
```tsx
{role&&isAdmin(role)&&<a href="/health" className="font-semibold text-slate-700 hover:underline">Health</a>}
```

- [ ] **Step 3: Run tests + build.** Run (from `frontend/`): `npx vitest run && npm run build` → all green (40 = 39 + 1); build succeeds; `/health` route generated.

- [ ] **Step 4: Commit.**

```bash
git add frontend/app/health/page.tsx frontend/app/page.tsx frontend/app/health.test.tsx
git commit -m "feat: admin System Health screen (status/stuck/errors/stage-latency) + nav link"
```

---

### Task 5: Docs

**Files:**
- Modify: `.env.example`, `docs/deployment.md`, `docs/roadmap.md`

- [ ] **Step 1: `.env.example`.** Add near the other flags:
```
# Observability: structured JSON logs (set "plain" for human-readable local logs)
LOG_FORMAT=json
# System-health thresholds (admin /admin/metrics + Health screen)
STUCK_PROCESSING_MINUTES=15
METRICS_RECENT_N=200
```

- [ ] **Step 2: `docs/deployment.md`.** Add an `## A5 — observability (lite)` section (after the A4 section): structured JSON logging (`LOG_FORMAT=json` default; set `plain` locally) with correlation fields (document_id/stage/latency_ms/actor); the admin-only `GET /admin/metrics` (status counts, recent errors, stuck-in-processing, per-stage p50/p95 latency from `pipeline_trace`); the admin **System Health** screen; note the stuck-processing view surfaces the "startup-only reset" gap on Render free; `STUCK_PROCESSING_MINUTES`/`METRICS_RECENT_N` knobs; no migration/deps; real external alerting remains the later full-B15.

- [ ] **Step 3: `docs/roadmap.md`.** In the A-track note, mark **A5 ✅ Shipped** and note **the A-track is complete**; the full **B15** (external metrics/alerting stack) remains a later `M`. Next candidates: the enterprise track (`org_id` tenant isolation, retention) or Phase 3 S4/S5.

- [ ] **Step 4: Commit.**

```bash
git add .env.example docs/deployment.md docs/roadmap.md
git commit -m "docs: A5 observability — env, deployment, roadmap (A-track complete)"
```

---

## Self-Review

**Spec coverage:** structured logging (`JsonFormatter`/`configure_logging` + config + startup) → Task 1. Correlation fields at pipeline/auto-approve points → Task 2. `GET /admin/metrics` (status_counts/errors_recent/stuck_processing/stage_latency) + `_percentile` → Task 3. System Health screen + nav link → Task 4. Docs → Task 5. Webhook structured-log point from the spec is folded into the logger being JSON-formatted (the existing webhook audit + `_log` calls already carry the info); the plan adds explicit `extra` at the pipeline/auto-approve points (the highest-value ones) — if the reviewer wants the webhook `extra` too it's a trivial add, noted.

**Placeholder scan:** No TBD/TODO. Every code step has complete code; tests include concrete assertions and seed data.

**Type consistency:** `configure_logging()`/`JsonFormatter` (Task 1) used in main startup + Task 2 log points share the `_EXTRA_KEYS` allow-list. `_percentile(values, p)` (Task 3) matches its test. `/admin/metrics` response shape (Task 3) matches the frontend `Metrics` type (Task 4). Config names `LOG_FORMAT`/`STUCK_PROCESSING_MINUTES`/`METRICS_RECENT_N` consistent across Tasks 1/3/5. Test counts cumulative (241 → ~249 backend; 39 → 40 frontend).
