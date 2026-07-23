# Real-time Agent Activity Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing right-side Agent Activity panel into a sequential, real-time playback of six agents driven by real extraction results, styled to the target mock (🤖 headers, ✓ / ⚠ markers, spinner while working).

**Architecture:** Extract the results→events mapping into a pure module (`frontend/lib/agent-timeline.ts`) unit-tested with Vitest. `page.tsx` calls the real `/upload` and `/process` endpoints, then replays the derived agent steps one at a time with a fixed delay so each agent visibly transitions spinner → ✓ / ⚠. No backend changes.

**Tech Stack:** Next.js 15, React 19, TypeScript, lucide-react (icons), Vitest (dev-only test runner, added here).

## Global Constraints

- Product name is **Aethermind** — never reintroduce "Atlas".
- No backend changes: `backend/app/services.py` and `backend/app/main.py` stay untouched.
- No WebSocket / SSE; real-time feel is client-side staging with a fixed delay.
- No new **runtime/UI** dependencies. Vitest is the only new dependency and is **dev-only**, scoped to `frontend/`.
- `STEP_DELAY_MS = 700` (single fixed constant; no delay-config UI).
- Node 22, ESM (`"type"` is absent in frontend package.json; Next uses ESM — Vitest runs ESM natively).
- Review-flag rule must match the backend exactly: a field is flagged when `confidence < 0.9 || !validated` (see `services.py:process_document`).

---

### Task 1: Add Vitest test runner to the frontend

The frontend (`frontend/package.json`) currently has no test runner. This task adds Vitest, a config, and a `test` script, verified by one trivial smoke test. It is isolated from the repo-root `vitest.config.ts`, which belongs to the unrelated Telegram-bot project.

**Files:**
- Modify: `frontend/package.json` (add `vitest` devDependency + `"test"` script)
- Create: `frontend/vitest.config.ts`
- Create: `frontend/lib/smoke.test.ts` (temporary smoke test, deleted in Step 6)

**Interfaces:**
- Consumes: nothing.
- Produces: a working `npm test` in `frontend/` that runs Vitest over `**/*.test.ts`.

- [ ] **Step 1: Add the smoke test (failing because no runner exists yet)**

Create `frontend/lib/smoke.test.ts`:

```ts
import { describe, it, expect } from "vitest";

describe("vitest smoke", () => {
  it("runs", () => {
    expect(1 + 1).toBe(2);
  });
});
```

- [ ] **Step 2: Run it to confirm there is no runner yet**

Run: `cd frontend && npm test`
Expected: FAIL — `npm error Missing script: "test"` (no test script / runner installed).

- [ ] **Step 3: Add Vitest config**

Create `frontend/vitest.config.ts`:

```ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["**/*.test.ts"],
    exclude: ["node_modules", ".next"],
  },
});
```

- [ ] **Step 4: Add the devDependency and script, then install**

In `frontend/package.json`, add a `"test": "vitest run"` entry to `"scripts"`, and add `"vitest": "^2.1.8"` to `"devDependencies"`. Then install:

Run: `cd frontend && npm install`
Expected: installs `vitest` without error.

- [ ] **Step 5: Run the smoke test to verify the runner works**

Run: `cd frontend && npm test`
Expected: PASS — 1 passed (`vitest smoke > runs`).

- [ ] **Step 6: Delete the smoke test and commit the setup**

Run: `cd frontend && rm lib/smoke.test.ts`

```bash
git add frontend/package.json frontend/package-lock.json frontend/vitest.config.ts
git commit -m "test: add Vitest runner to frontend

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Pure agent-timeline module (TDD)

Build the pure logic that maps extraction results to the ordered agent steps. No React. This is the only unit-tested piece.

**Files:**
- Create: `frontend/lib/agent-timeline.ts`
- Test: `frontend/lib/agent-timeline.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces (imported by Task 3):
  - `type AgentStatus = "working" | "complete" | "attention"`
  - `type AgentStep = { agent: string; message: string; status: AgentStatus }`
  - `type TimelineField = { field_name: string; field_value: string | null; confidence: number; validated: boolean }`
  - `function titleize(snake: string): string`
  - `function deriveAgentTimeline(input: { schemaName: string; fields: TimelineField[] }): AgentStep[]` — returns 5 steps in order: Intake, Classification, Extraction, Validation, Review (Export is NOT included; it fires on a separate user action).

- [ ] **Step 1: Write the failing tests**

Create `frontend/lib/agent-timeline.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { deriveAgentTimeline, titleize, type TimelineField } from "./agent-timeline";

const field = (over: Partial<TimelineField>): TimelineField => ({
  field_name: "vendor_name",
  field_value: "Acme",
  confidence: 0.96,
  validated: true,
  ...over,
});

const byAgent = (steps: ReturnType<typeof deriveAgentTimeline>, agent: string) =>
  steps.find((s) => s.agent === agent)!;

describe("titleize", () => {
  it("title-cases snake_case", () => {
    expect(titleize("vendor_name")).toBe("Vendor Name");
  });
  it("uppercases known acronym tokens", () => {
    expect(titleize("gst_number")).toBe("GST Number");
    expect(titleize("po_number")).toBe("PO Number");
  });
});

describe("deriveAgentTimeline", () => {
  it("returns the 5 processing agents in order, Export excluded", () => {
    const steps = deriveAgentTimeline({ schemaName: "Invoice", fields: [field({})] });
    expect(steps.map((s) => s.agent)).toEqual([
      "Intake Agent",
      "Classification Agent",
      "Extraction Agent",
      "Validation Agent",
      "Review Agent",
    ]);
  });

  it("all valid, high confidence → validation & review complete", () => {
    const fields = [field({}), field({ field_name: "total", field_value: "100", confidence: 0.94 })];
    const steps = deriveAgentTimeline({ schemaName: "Invoice", fields });
    expect(byAgent(steps, "Classification Agent").message).toBe("Invoice detected");
    expect(byAgent(steps, "Extraction Agent").message).toBe("2 fields extracted");
    expect(byAgent(steps, "Validation Agent")).toMatchObject({
      status: "complete",
      message: "All fields passed validation",
    });
    expect(byAgent(steps, "Review Agent")).toMatchObject({
      status: "complete",
      message: "No human approval required",
    });
  });

  it("one missing required field → validation names it, review flags 1", () => {
    const fields = [
      field({}),
      field({ field_name: "gst_number", field_value: null, confidence: 0.62, validated: false }),
    ];
    const steps = deriveAgentTimeline({ schemaName: "Invoice", fields });
    expect(byAgent(steps, "Validation Agent")).toMatchObject({
      status: "attention",
      message: "GST Number not found",
    });
    expect(byAgent(steps, "Review Agent")).toMatchObject({
      status: "attention",
      message: "1 field requires human approval",
    });
  });

  it("three+ failing → lists first two + N more", () => {
    const fields = [
      field({ field_name: "gst_number", field_value: null, validated: false, confidence: 0.6 }),
      field({ field_name: "po_number", field_value: null, validated: false, confidence: 0.6 }),
      field({ field_name: "due_date", field_value: null, validated: false, confidence: 0.6 }),
    ];
    const steps = deriveAgentTimeline({ schemaName: "Invoice", fields });
    expect(byAgent(steps, "Validation Agent").message).toBe("GST Number, PO Number not found +1 more");
    expect(byAgent(steps, "Review Agent").message).toBe("3 fields require human approval");
  });

  it("low confidence but validated → review flags, validation passes", () => {
    const fields = [field({ field_name: "total", field_value: "100", confidence: 0.7, validated: true })];
    const steps = deriveAgentTimeline({ schemaName: "Invoice", fields });
    expect(byAgent(steps, "Validation Agent").status).toBe("complete");
    expect(byAgent(steps, "Review Agent")).toMatchObject({
      status: "attention",
      message: "1 field requires human approval",
    });
  });

  it("zero fields → 0 extracted, no crash, all pass", () => {
    const steps = deriveAgentTimeline({ schemaName: "Invoice", fields: [] });
    expect(byAgent(steps, "Extraction Agent").message).toBe("0 fields extracted");
    expect(byAgent(steps, "Validation Agent").status).toBe("complete");
    expect(byAgent(steps, "Review Agent").status).toBe("complete");
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npm test`
Expected: FAIL — cannot resolve `./agent-timeline` (module does not exist yet).

- [ ] **Step 3: Implement the module**

Create `frontend/lib/agent-timeline.ts`:

```ts
export type AgentStatus = "working" | "complete" | "attention";
export type AgentStep = { agent: string; message: string; status: AgentStatus };
export type TimelineField = {
  field_name: string;
  field_value: string | null;
  confidence: number;
  validated: boolean;
};

const UPPERCASE_TOKENS = new Set(["gst", "po", "bol", "erp", "id"]);

export function titleize(snake: string): string {
  return snake
    .split("_")
    .map((w) =>
      UPPERCASE_TOKENS.has(w.toLowerCase())
        ? w.toUpperCase()
        : w.charAt(0).toUpperCase() + w.slice(1),
    )
    .join(" ");
}

function plural(n: number, singular: string): string {
  return `${n} ${singular}${n === 1 ? "" : "s"}`;
}

export function deriveAgentTimeline(input: {
  schemaName: string;
  fields: TimelineField[];
}): AgentStep[] {
  const { schemaName, fields } = input;

  const failing = fields.filter(
    (f) => !f.validated || f.field_value == null || f.field_value === "",
  );
  let validation: AgentStep;
  if (failing.length === 0) {
    validation = { agent: "Validation Agent", status: "complete", message: "All fields passed validation" };
  } else {
    const shown = failing.slice(0, 2).map((f) => titleize(f.field_name)).join(", ");
    const extra = failing.length > 2 ? ` +${failing.length - 2} more` : "";
    validation = { agent: "Validation Agent", status: "attention", message: `${shown} not found${extra}` };
  }

  const flagged = fields.filter((f) => f.confidence < 0.9 || !f.validated).length;
  const review: AgentStep = flagged
    ? {
        agent: "Review Agent",
        status: "attention",
        message: `${plural(flagged, "field")} require${flagged === 1 ? "s" : ""} human approval`,
      }
    : { agent: "Review Agent", status: "complete", message: "No human approval required" };

  return [
    { agent: "Intake Agent", status: "complete", message: "Document received" },
    { agent: "Classification Agent", status: "complete", message: `${schemaName} detected` },
    { agent: "Extraction Agent", status: "complete", message: `${plural(fields.length, "field")} extracted` },
    validation,
    review,
  ];
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npm test`
Expected: PASS — all tests in `agent-timeline.test.ts` green.

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/agent-timeline.ts frontend/lib/agent-timeline.test.ts
git commit -m "feat: add pure agent-timeline derivation with tests

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Wire staged real-time playback into the dashboard

Refactor `page.tsx` to import the module, run Intake against the real `/upload`, replay Classification→Review after `/process` with a fixed delay, and render 🤖 / ✓ / ⚠. Verified in the browser (visual/timing behavior).

**Files:**
- Modify: `frontend/app/page.tsx`

**Interfaces:**
- Consumes from Task 2: `deriveAgentTimeline`, `AgentStatus`, `AgentStep` from `../lib/agent-timeline`.
- Produces: no exports; updates the running UI.

- [ ] **Step 1: Import the module and drop the local AgentStatus definition**

In `frontend/app/page.tsx`, add to the imports near the top (after the `api` import):

```tsx
import { deriveAgentTimeline, type AgentStatus, type AgentStep } from "../lib/agent-timeline";
```

Then delete the now-duplicate local type line:

```tsx
type AgentStatus="working"|"complete"|"attention";
```

Keep `type AgentEvent={id:number;agent:string;message:string;status:AgentStatus};` (it reuses the imported `AgentStatus`).

- [ ] **Step 2: Add the playback helpers above `upload`**

Inside the `Home` component, immediately before the `async function upload(file:File){...}` declaration, add:

```tsx
 const STEP_DELAY_MS=700;
 const wait=(ms:number)=>new Promise<void>(res=>window.setTimeout(res,ms));
 async function playTimeline(steps:AgentStep[]){
   for(const step of steps){
     const id=++activityId.current;
     setAgentEvents(events=>[...events,{id,agent:step.agent,message:"Working…",status:"working"}]);
     await wait(STEP_DELAY_MS);
     setAgentEvents(events=>events.map(e=>e.id===id?{...e,status:step.status,message:step.message}:e));
   }
 }
```

- [ ] **Step 3: Replace the body of `upload` with the staged flow**

Replace the entire existing `async function upload(file:File){...}` (the single long line) with:

```tsx
 async function upload(file:File){
   setLoading(true);setMessage("Uploading document…");setAgentEvents([]);
   try{
     const intakeId=++activityId.current;
     setAgentEvents([{id:intakeId,agent:"Intake Agent",message:"Working…",status:"working"}]);
     const form=new FormData();form.append("file",file);
     const doc=await api(`/upload?document_type=${type}`,{method:"POST",body:form});
     setAgentEvents(events=>events.map(e=>e.id===intakeId?{...e,status:"complete",message:"Document received"}:e));
     setMessage("Extracting text, applying schema, validating fields…");
     const complete=await api(`/process/${doc.id}`,{method:"POST"});
     const schemaName=schemas.find(s=>s.key===type)?.name||type.replaceAll("_"," ");
     const steps=deriveAgentTimeline({schemaName,fields:complete.fields}).filter(s=>s.agent!=="Intake Agent");
     await playTimeline(steps);
     setSelected(complete);
     setMessage("Processing complete. Review flagged fields before approval.");
     refresh();
   }catch(e:any){
     setAgentEvents(events=>events.map(ev=>ev.status==="working"?{...ev,status:"attention",message:e.message||"Failed"}:ev));
     setMessage(e.message||"Upload failed. Please retry.");
   }finally{setLoading(false)}
 }
```

Note: the Export agent stays exactly as-is — it is still fired from the `Review` component's `onExport` callback (`begin("Export Agent",...)`/`finish(...)`). Do not change that.

- [ ] **Step 4: Add the 🤖 prefix in the `AgentActivity` renderer**

In the `AgentActivity` function, find the agent-name line:

```tsx
<p className="text-sm font-semibold">{event.agent}</p>
```

Replace it with:

```tsx
<p className="text-sm font-semibold">🤖 {event.agent}</p>
```

(The ✓ / ⚠ / spinner icons already exist in `AgentActivity`: `CheckCircle2` for `complete`, `TriangleAlert` for `attention`, spinning `CircleDashed` for `working`. No icon change needed.)

- [ ] **Step 5: Verify the build/types pass**

Run: `cd frontend && npx tsc --noEmit`
Expected: no type errors.

- [ ] **Step 6: Manually verify in the browser**

Start backend (`cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000`) and frontend (`cd frontend && npm run dev`), open `http://localhost:3000`, upload `docs/sample-invoice.txt` saved as a PDF (or a file from `uploads/`).
Expected: the right panel reveals agents one at a time — 🤖 Intake ✓ → 🤖 Classification "Invoice detected" ✓ → 🤖 Extraction "N fields extracted" ✓ → 🤖 Validation ✓ or ⚠ with field names → 🤖 Review ✓ or ⚠, each pausing ~0.7s with a spinner before its icon resolves. CSV export still appends 🤖 Export ✓.

- [ ] **Step 7: Commit**

```bash
git add frontend/app/page.tsx
git commit -m "feat: staged real-time agent activity playback

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the implementer

- The `activityId` ref and `setAgentEvents` state already exist in `Home`; reuse them.
- `schemas` state is already loaded on mount via `refresh()`; `schemas.find(...)` is safe.
- Keep the existing dense one-line code style of `page.tsx` — match the surrounding formatting.
