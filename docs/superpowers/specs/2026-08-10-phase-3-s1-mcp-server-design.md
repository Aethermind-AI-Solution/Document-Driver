# Phase 3 (S1) — Aethermind as an MCP Server Design

**Date:** 2026-08-10
**Status:** Approved design (pre-plan)
**Roadmap item:** Phase 3, slice **S1** — expose Aethermind's extraction as MCP tools other agents can call. Slices S2 (Drive/Gmail ingestion), S3 (webhook/ERP push), S4 (enrichment) are separate later cycles.

## Goal

Let an external agent (a client's Claude/agent) call Aethermind directly over MCP: list document types, submit a document for extraction, fetch results, and correct a field — all as MCP tools served from our existing FastAPI at `/mcp`, gated by a shared token.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| First slice | S1 — Aethermind as an MCP **server** (self-contained, no third-party OAuth) |
| Implementation | Official **MCP Python SDK (FastMCP)**, mounted as an ASGI sub-app on FastAPI at `/mcp` |
| Transport | FastMCP's current **Streamable HTTP** (the 2026 stateless refinement is a later optimization) |
| Auth | Single env **`MCP_API_TOKEN`** (Bearer) → a fixed service principal with role `MCP_SERVICE_ROLE` (default `reviewer`) |
| Enablement | MCP server is **only mounted when `MCP_API_TOKEN` is set** (off by default) |
| Tools (MVP) | `list_document_types`, `extract_document`, `get_document`, `submit_correction` |
| Document input | **base64** bytes in the tool arg (no server-side URL fetch → no SSRF) |
| Pipeline | `extract_document` runs `run_pipeline` **inline** (`await`) and returns the result |
| Audit | MCP mutations logged with `actor_id=None`, `actor_email="mcp-service"` |
| Frontend | **Out of scope** — machine-facing API; documented instead |

## Non-goals (this cycle)

- Per-client API tokens / multi-tenant token management (single shared token for now).
- The 2026 stateless-transport optimization (use the SDK's current transport; fine on a single instance).
- MCP Tasks primitive for very long jobs; an `approve_document` tool; frontend UI.
- Slices S2/S3/S4.

---

## Architecture

- **New module `backend/app/mcp_server.py`:**
  - Builds a `FastMCP` instance and registers the four tools.
  - Exposes `mcp_asgi_app()` → the FastMCP Streamable-HTTP ASGI app, wrapped by a small auth ASGI middleware.
  - `build_service_principal()` → a lightweight object with `.id = None`, `.email = "mcp-service"`, `.role = config.MCP_SERVICE_ROLE` used for audit stamping.
- **Mounting (`backend/app/main.py`):** if `config.MCP_API_TOKEN`, `app.mount("/mcp", mcp_asgi_app())`. If unset, do not mount (endpoint absent → 404).
- **Auth wrapper:** a thin ASGI middleware around the mounted MCP app that reads `Authorization: Bearer <token>`, constant-time compares to `config.MCP_API_TOKEN`, returns `401` on mismatch/absence, else delegates. (Isolated + unit-testable as a callable.)
- **DB access in tools:** each tool opens its own `SessionLocal()` (request-independent, like `jobs.run_pipeline_task`) and closes it in `finally`.
- **Config additions:** `MCP_API_TOKEN` (default `""` → disabled), `MCP_SERVICE_ROLE` (default `"reviewer"`).
- **Dependency:** the official `mcp` package, pinned in `backend/requirements.txt`.

## Tools

### `list_document_types() -> list[dict]`
Returns `available_schemas(db)` shape: `[{key, name}]` (drop the heavy `fields` for brevity; keys are what `extract_document` accepts).

### `extract_document(file_base64: str, filename: str, document_type: str = "auto") -> dict`
1. Decode `file_base64` (error → clear tool error).
2. Validate suffix of `filename` ∈ {.pdf,.png,.jpg,.jpeg}; validate `document_type` is a known schema or `"auto"`.
3. `storage.get_storage().save(safe_name, data)` → key; create `Document(status="uploaded", stored_path=key, document_type=hint)` where `hint = "invoice"` if `"auto"` else the given type (the classifier still auto-detects inside the pipeline; `"auto"` maps to the default hint).
4. `await run_pipeline(db, doc, doc.document_type, actor=service_principal)` (inline).
5. Return `{document_id, status, confidence, document_type, fields: [{field_name, field_value, confidence, grounded}], anomalies}`.

### `get_document(document_id: int) -> dict`
Load the doc; `404`-equivalent tool error if missing. Return the same shape as `extract_document`'s result plus `pipeline_trace`.

### `submit_correction(document_id: int, field_name: str, value: str) -> dict`
Find the `ExtractedField(document_id, field_name)`; if missing → tool error. Set `field_value=value`, `edited_by_user=True`; `log(..., "Edited", f"MCP correction: {field_name}", actor=service_principal)`; commit. Return the updated field.

## Auth & audit

- The `/mcp` sub-app is unreachable without a correct `MCP_API_TOKEN` Bearer (constant-time compare). One shared token; all tools run at `MCP_SERVICE_ROLE`.
- Every MCP mutation (`extract_document`'s pipeline "Processed" log, `submit_correction`'s "Edited") is stamped `actor_email="mcp-service"`, `actor_id=None` — reuses the Phase-1 actor-stamped `AuditLog`, so the audit trail distinguishes MCP-originated actions from human users.

## Prompt-injection surface

Tools return **structured data, not instructions**. `extract_document` runs the same pipeline with existing grounding + injection defenses; its output is fields/values an agent consumes as data. No new injection vector is introduced. Documented so the boundary stays intentional; the base64-only input avoids server-side URL fetching (no SSRF).

## Error handling

- Missing/invalid `MCP_API_TOKEN` on a request → `401` (auth wrapper).
- MCP not enabled (no token configured) → `/mcp` not mounted → `404`.
- Bad base64 / unsupported file type / unknown `document_type` / unknown `document_id` / unknown field → clear tool errors (FastMCP surfaces raised exceptions as MCP errors); validate inputs explicitly.
- Pipeline failure inside `extract_document` → `run_pipeline` sets `status="error"` and re-raises; the tool returns an error (or the doc in `error` status) — surfaced to the caller.
- Long docs may exceed the platform proxy timeout while the tool runs inline — acceptable for MVP (typical single/few-page docs); async/Tasks is a documented future upgrade.

## Testing (offline)

- **Tools as functions** (they're plain/async — call directly with a test session + stubs):
  - `list_document_types` returns the builtin schemas (key+name).
  - `extract_document` with `run_pipeline`, classifier, and storage stubbed → returns `document_id` + fields; bad base64 / bad suffix / unknown type raise clear errors.
  - `get_document` round-trips a seeded doc; unknown id errors.
  - `submit_correction` updates the field + sets `edited_by_user` + writes an audit row with `actor_email="mcp-service"`; unknown field errors.
- **Auth wrapper** (unit): no/invalid Bearer → 401; correct Bearer → delegates (call the wrapper with a stub inner app).
- **Mount smoke test:** with `MCP_API_TOKEN` set, `mcp_asgi_app()` builds and the `mcp` package imports; with it unset, `main` does not mount `/mcp`.
- Keep backend 105 / frontend 26 green (net new tests on top). All offline — no real MCP client handshake, no network, no LLM.

## Build sequencing (one plan, ordered tasks)

1. **De-risk + config:** add the pinned `mcp` dep + `MCP_API_TOKEN`/`MCP_SERVICE_ROLE` config; a smoke test that `mcp`/FastMCP imports and a trivial FastMCP app + its ASGI app build.
2. **Auth wrapper** (constant-time token ASGI middleware) + unit tests.
3. **Tools** `list_document_types` + `get_document` + `submit_correction` (read/correct; no pipeline) + tests.
4. **`extract_document`** (storage + inline `run_pipeline`) + tests.
5. **Mount on FastAPI** (conditional on token) + `build_service_principal` + audit wiring + mount tests.
6. **Docs:** `.env.example` (`MCP_API_TOKEN`, `MCP_SERVICE_ROLE`), `docs/deployment.md` (endpoint, tools, how to enable), `docs/roadmap.md` (S1 shipped; S2–S4 remain).

## Deployment notes

- New env: `MCP_API_TOKEN` (set a strong value to enable the MCP server), `MCP_SERVICE_ROLE` (default `reviewer`). New dep: `mcp` (pinned). No schema change. Render redeploys via the existing flow. The MCP endpoint is `POST <backend>/mcp` (Streamable HTTP), Bearer `MCP_API_TOKEN`.
