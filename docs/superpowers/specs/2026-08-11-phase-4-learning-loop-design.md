# Phase 4 (Slice 1) — Learning Loop from Corrections Design

**Date:** 2026-08-11
**Status:** Approved design (pre-plan)
**Roadmap item:** Phase 4, first slice — learning loop (B14). Schema-Author (dynamic schemas, F4) and deeper confidence (B4+) are separate later slices.

## Goal

Make extraction improve over time by feeding **past human corrections back into the extractor as few-shot hints** — no fine-tuning, no new infrastructure. When Aethermind extracts a document, it consults corrections operators made on previously **approved** documents of the same type and nudges the model toward the reviewer's judgment.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Mechanism | Few-shot correction hints injected into the extractor prompt (no fine-tuning) |
| Signal source | **Approved-doc corrections only** (`status=="approved"`, `edited_by_user`, value changed) |
| Scope of learning | **Same document type only** (invoice corrections help invoices) |
| Where | In the pipeline's extractor stage; hints fetched once per document, passed into `ai_extract` |
| Bounds | `LEARNING_MAX_HINTS_PER_FIELD` (default 3), `LEARNING_MAX_HINTS` global (default 20) |
| Toggle | `LEARNING_ENABLED` (default `True`) |
| Visibility | Extractor trace stage notes `"N correction hints applied"` |

## Non-goals (this slice)

- Fine-tuning / model training; deterministic correction-rule post-processing.
- Cross-type / global formatting hints (same-type only).
- Confidence changes (B4+); Schema-Author (F4); any UI beyond the trace note.

---

## Where it plugs in

Pipeline today: classifier → per-page extractor fan-out (`services.ai_extract`) → reconciler → validator. The extractor stage gains a pre-step: fetch correction hints for the classified type + schema fields (once), then pass them into every per-page `ai_extract` call. `run_pipeline`, reconciler, validator are otherwise unchanged. The regex fallback ignores hints.

- `PipelineContext` gains a `hints` field (default empty).
- `ExtractorAgent.run`: if `config.LEARNING_ENABLED`, `ctx.hints = get_correction_hints(ctx.db, ctx.document.document_type, ctx.schema["fields"])`; fan out passes `ctx.hints` to `ai_extract`; the stage `detail` includes the hint count.

## Correction retrieval

New `services.get_correction_hints(db, document_type, fields) -> dict[str, list[tuple[str, str]]]`:
- Query `ExtractedField` joined to `Document` where:
  `Document.document_type == document_type` AND `Document.status == "approved"` AND `ExtractedField.edited_by_user == True` AND `ExtractedField.original_value IS NOT NULL` AND `ExtractedField.field_value IS NOT NULL` AND `ExtractedField.original_value != ExtractedField.field_value` AND `ExtractedField.field_name` in the schema's field names — ordered most-recent-first (by `Document.upload_date` desc / id desc).
- Group by `field_name`; **dedup** identical `(original_value, field_value)` pairs; keep at most `LEARNING_MAX_HINTS_PER_FIELD` per field and `LEARNING_MAX_HINTS` total.
- Returns `{}` when there are no qualifying corrections (clean cold-start).
- Wrapped so any query error returns `{}` (never breaks extraction).

## Prompt injection

- `services._hint_block(hints: dict) -> str` formats the hints into a plain text block, or `""` when empty:
  ```
  Human reviewers have previously corrected extractions for this document type. Learn the pattern:
  - invoice_number: model extracted "INV 123" → correct value was "INV-123"
  - seller_name: "Acme" → "Acme Corp Pvt Ltd"
  Apply the same judgment, but ALWAYS extract the value that THIS document actually contains — never copy a past value that is not present in this document.
  ```
- `services.ai_extract(text, fields, source_path, hints=None)` threads `hints` to `openai_extract`/`gemini_extract`; those append `_hint_block(hints)` as an extra `input_text` block in the request content when non-empty. `fallback_extract` ignores hints.
- The corrections are **trusted data** (authored by authenticated reviewers, stored in our DB), not untrusted document content — but the block is still formatted as plain guidance, and the "extract the value THIS document contains" guard prevents the model from blindly emitting a past value.

## Config, cold-start, cost, safety

- Config additions: `LEARNING_ENABLED` (bool, default `True`), `LEARNING_MAX_HINTS_PER_FIELD` (int, default 3), `LEARNING_MAX_HINTS` (int, default 20).
- **Cold-start:** no approved corrections → `get_correction_hints` returns `{}` → `_hint_block` returns `""` → prompt identical to today.
- **Failure isolation:** hint fetch and formatting are guarded; on error, extraction proceeds with no hints.
- **Cost:** a bounded token increase on the extractor prompt only (capped by the two limits). No new external calls.

## Error handling

- Query/DB error in `get_correction_hints` → `{}` (logged to stderr, extraction continues).
- `LEARNING_ENABLED=False` → hints never fetched; behavior identical to pre-Phase-4.
- Empty/whitespace values excluded (the `original_value != field_value` + non-null filters).

## Testing (offline)

- **`get_correction_hints`:** seed approved docs with edited, changed fields of a type → returns `(original, corrected)` pairs per field. Excludes: non-approved docs, `edited_by_user=False`, unchanged values, null values, other document types. Respects `LEARNING_MAX_HINTS_PER_FIELD` and the global cap. Dedups identical pairs. Returns `{}` when none.
- **`_hint_block`:** returns `""` for `{}`; includes each field's `original → corrected` line and the "extract from THIS document" guard for non-empty input.
- **`openai_extract` injection:** with the OpenAI client stubbed to capture its `input`, a call with `hints` includes the hint block text in the request content; a call without hints does not. (No real network.)
- **Extractor stage:** `ExtractorAgent.run` with `LEARNING_ENABLED=True` fetches hints (stub `get_correction_hints`) and passes them to `ai_extract` (spy the `hints` arg); with `LEARNING_ENABLED=False`, hints are `{}`/not fetched. Trace detail reflects the hint count.
- **Cold-start:** no corrections → extraction output unchanged vs. no-hints.
- Keep backend 122 / frontend 26 green.

## Build sequencing (one plan, ordered tasks)

1. Config flags + `get_correction_hints` (query + caps + dedup + guarded) with tests.
2. `_hint_block` formatter + `ai_extract`/`openai_extract` (and `gemini_extract`) `hints` param + injection, with tests (stubbed client).
3. `PipelineContext.hints` + `ExtractorAgent` fetch-and-pass + trace note, with tests.
4. Docs: `.env.example`, `docs/deployment.md`, `docs/roadmap.md` (mark the learning-loop slice shipped; note Schema-Author + confidence remain).

## Deployment notes

No new dependencies, no schema change (reuses `original_value`/`edited_by_user`/`status`). New optional env: `LEARNING_ENABLED`, `LEARNING_MAX_HINTS_PER_FIELD`, `LEARNING_MAX_HINTS` (all defaulted). Render redeploys via the existing flow. The loop only has an effect once documents have been corrected **and approved**.
