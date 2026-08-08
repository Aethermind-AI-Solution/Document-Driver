export type AgentStatus = "working" | "complete" | "attention";
export type AgentStep = { agent: string; message: string; status: AgentStatus };
export type TimelineField = {
  field_name: string;
  field_value: string | null;
  confidence: number;
  validated: boolean;
};

const UPPERCASE_TOKENS = new Set(["gst", "gstin", "po", "bol", "erp", "id"]);

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

export type TraceEntry = { name: string; status: string; detail: string; duration_ms: number };

export function traceToSteps(trace: TraceEntry[]): AgentStep[] {
  const map: Record<string, AgentStatus> = { ok: "complete", attention: "attention", error: "attention" };
  return trace.map((t) => ({
    agent: t.name,
    status: map[t.status] ?? "complete",
    message: t.detail || t.name,
  }));
}
