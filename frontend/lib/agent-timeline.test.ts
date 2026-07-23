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
