import { describe, it, expect } from "vitest";
import { computeRoi } from "../lib/roi";

describe("computeRoi", () => {
  it("computes saved docs, dollars, and hours from buyer inputs", () => {
    const out = computeRoi({ monthlyVolume: 50000, costPerDoc: 2, minutesPerDoc: 4, stpRate: 0.6 });
    expect(out.savedDocs).toBe(30000);
    expect(out.monthlySaved).toBe(60000);
    expect(out.hoursSaved).toBe(2000);
  });
  it("handles zero stpRate", () => {
    expect(computeRoi({ monthlyVolume: 100, costPerDoc: 2, minutesPerDoc: 4, stpRate: 0 }).monthlySaved).toBe(0);
  });
});
