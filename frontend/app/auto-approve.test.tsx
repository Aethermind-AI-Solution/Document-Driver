// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import AutoApprovePage from "./auto-approve/page";

vi.mock("../lib/api", () => ({
  me: vi.fn(async () => ({ role: "admin" })),
  api: vi.fn(async (path: string) => {
    if (path === "/auto-approve")
      return [{ id: 1, document_type: "invoice", enabled: false, min_confidence: 0.95, created_at: "2024-01-01T00:00:00Z" }];
    if (path === "/auto-approve/eval/invoice")
      return {
        n: 5,
        header: "Upper bound based on reviewer overrides; not a live error rate.",
        per_field: {},
        reliability: {},
        grounded_but_wrong: { n_high_conf: 3, n_wrong: 1, rate: 0.1 },
      };
    return {};
  }),
}));

describe("AutoApprovePage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("lists a config with its eval safety snapshot and disables enabling on insufficient data", async () => {
    render(<AutoApprovePage />);

    await waitFor(() => expect(screen.getByText(/invoice/i)).toBeTruthy());

    // Safety snapshot: sample size and grounded-but-wrong rate rendered.
    await waitFor(() => expect(screen.getByText(/5/)).toBeTruthy());
    expect(screen.getByText(/10%/)).toBeTruthy();
    expect(screen.getByText(/insufficient data/i)).toBeTruthy();

    // Enable control is disabled because n (5) is below MIN_N.
    const toggle = screen.getByLabelText(/enable auto-approve for invoice/i) as HTMLInputElement;
    expect(toggle.disabled).toBe(true);
  });
});
