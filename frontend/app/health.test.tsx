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
