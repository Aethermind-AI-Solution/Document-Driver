# Real-time Agent Activity Panel — Design

**Date:** 2026-07-23
**Component:** Aethermind Document Intelligence Engine — frontend
**Status:** Approved (design)

## Problem

The document workflow currently runs as a single burst: `page.tsx` calls `/upload`
then `/process`, and fires all six agent events almost simultaneously once
`/process` returns. The right-side `AgentActivity` panel exists but does not
visibly "play out," so the product does not feel agentic.

## Goal

Make the existing right-side `AgentActivity` panel reveal the six agents
sequentially and in real time, driven by actual upload/extraction results, and
styled to match the target mock (🤖 headers, ✓ / ⚠ markers, animated spinner
while working).

## Non-goals (YAGNI)

- No backend changes (`services.py`, `main.py` untouched).
- No WebSocket / Server-Sent Events. Real-time feel is client-side staging.
- No persistence of agent events across reloads.
- No configurable delay UI — a single fixed constant.
- No new **runtime/UI** dependencies. (A dev-only test runner is added — see
  Testing — because the frontend currently has none and TDD requires one.)

## Approach

**Staged client-side reveal.** Keep the real API calls (`/upload`, `/process`),
but replay the agent timeline with a small fixed delay between steps so each
agent visibly transitions **spinner → ✓ / ⚠**, one at a time.

### The six agents

Messages are derived from real data, never hardcoded (except static labels).

| Agent | Success (✓) | Warning (⚠) |
|-------|-------------|-------------|
| 🤖 Intake Agent | Document received | — |
| 🤖 Classification Agent | `{SchemaName}` detected | — |
| 🤖 Extraction Agent | `{n}` fields extracted | — |
| 🤖 Validation Agent | All fields passed validation | `{FieldName}` not found (+ list of failing fields) |
| 🤖 Review Agent | No human approval required | `{n}` field(s) require human approval |
| 🤖 Export Agent | ERP payload generated | — (fires on CSV export, unchanged) |

- **Classification** uses the selected schema's display name (e.g. "Invoice").
- **Extraction** uses the real `complete.fields.length`.
- **Validation** inspects real fields where `validated === false` or
  `field_value` is empty/null. It names the specific failing fields
  (e.g. "GST Number not found"). If more than two fail, it shows the first two
  followed by "+N more". If none fail, it reports success.
- **Review** counts fields flagged for human review, defined as
  `confidence < 0.9 || !validated` (matching the backend's `review_required`
  logic in `services.py:process_document`).

## Architecture

Changes touch `frontend/app/page.tsx` (wiring + rendering), a new pure-logic
module `frontend/lib/agent-timeline.ts`, its test, and dev-only test config
(`frontend/package.json`, `frontend/vitest.config.ts`). No backend files change.

### Pure module: `frontend/lib/agent-timeline.ts`

Extract the results-to-events mapping into a pure, browser-independent module
(no React imports) so it can be unit-tested (TDD step). It exports the shared
types (`AgentStatus`, `AgentStep`), the `titleize` helper, and
`deriveAgentTimeline`. `page.tsx` imports these instead of redefining them.
Signature:

```ts
type AgentStatus = "working" | "complete" | "attention";
type AgentStep = { agent: string; message: string; status: AgentStatus };

function deriveAgentTimeline(input: {
  schemaName: string;        // e.g. "Invoice"
  fields: ExtractedField[];  // from /process response
}): AgentStep[];
```

- Returns the ordered list of the **five** processing agents (Intake through
  Review) with their final `status` and `message`. The Export agent is excluded
  because it fires on a separate user action (CSV export), not during processing.
- Each field is `{ field_name, field_value, confidence, validated }`.
- Field-name formatting for messages: snake_case → Title Case
  (e.g. `gst_number` → "GST Number"). Reuse a small `titleize` helper; GST-style
  all-caps tokens are handled by an uppercase set (`gst`, `po`, `bol`, `erp`).

### Playback: `playTimeline`

An async driver that, given the derived steps, renders them one at a time:

1. Push the step in `working` status (spinner shown).
2. `await delay(STEP_DELAY_MS)` — fixed constant, `STEP_DELAY_MS = 700`.
3. Update the step to its final `complete` / `attention` status + message.
4. Proceed to the next step.

`upload()` is refactored to:
1. Clear `agentEvents`.
2. `await api('/upload')` — real call.
3. Play Intake as complete.
4. `await api('/process/{id}')` — real call.
5. `deriveAgentTimeline(...)` on the real response, then `playTimeline(...)` for
   Classification → Extraction → Validation → Review.
6. Set `selected` to the processed doc and update the status message.

Export stays where it is today: the Export agent event fires from the Review
component's `onExport` callback.

### Rendering

`AgentActivity` is updated so each event shows:
- 🤖 emoji prefixing the agent name.
- Spinner (existing `CircleDashed animate-spin`) while `working`.
- ✓ emerald `CheckCircle2` when `complete`.
- ⚠ amber `TriangleAlert` when `attention`.

## Error handling

- If `/upload` or `/process` throws, playback stops and the current in-flight
  agent is marked `attention` with the error message; the existing `catch` in
  `upload()` still sets the top-level `message`. No partial success is faked.
- `deriveAgentTimeline` with zero fields returns Extraction "0 fields extracted"
  and Validation success (nothing to fail) — no crash.

## Testing

The frontend currently has **no test runner**. Add Vitest as a dev-only
dependency to `frontend/package.json`, a `frontend/vitest.config.ts`, and a
`"test": "vitest run"` script. This is isolated from the root `vitest.config.ts`,
which belongs to the unrelated Telegram-bot project.

Unit tests for `deriveAgentTimeline` only — it is the pure logic. File:
`frontend/lib/agent-timeline.test.ts`. Cases:

1. All fields valid, high confidence → Validation ✓, Review ✓ ("No human
   approval required").
2. One required field missing (`field_value` null, `validated` false) → Validation
   ⚠ names that field; Review ⚠ "1 field requires human approval".
3. Three+ fields failing → Validation ⚠ lists first two + "+N more".
4. Low confidence but validated → Review ⚠ (flagged by `confidence < 0.9`).
5. Zero fields → Extraction "0 fields extracted", no crash.

`playTimeline` and rendering are verified manually in the browser (staging delay
and spinner→icon transition are visual concerns).

## Rollback

Single-file change to `page.tsx` plus one test file. Revert the commit to restore
prior behavior.
