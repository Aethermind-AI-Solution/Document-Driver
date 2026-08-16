// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import Home from "./page";

const approvedDoc = {
  id: 3, filename: "inv.pdf", document_type: "invoice", status: "approved",
  confidence: 0.95, upload_date: "2026-08-15", review_required: false, anomalies: [],
  fields: [{ id: 1, field_name: "total", field_value: "100", confidence: 0.95, validated: true, grounded: "grounded" }],
  audit: [],
};
const apiMock = vi.fn(async (path: string, init?: any) => {
  if (path === "/auth/me") return { role: "admin", email: "a@b.co" };
  if (path === "/schemas") return [];
  if (path.startsWith("/documents/stats")) return { total: 1, review_required: 0, avg_processing_time: 1 };
  if (path.startsWith("/documents")) return { items: [approvedDoc], total: 1 };
  if (path.startsWith("/document/")) return approvedDoc;
  return {};
});
vi.mock("../lib/api", () => ({
  api: (p: string, i?: any) => apiMock(p, i),
  me: vi.fn(async () => ({ role: "admin", email: "a@b.co" })),
  pollDocument: vi.fn(), downloadFile: vi.fn(), TERMINAL: ["processed", "review_required", "error"],
}));

describe("reopen flow", () => {
  beforeEach(() => vi.clearAllMocks());
  it("shows Reopen (not Approve) on an approved doc and sends action:reopen after confirm", async () => {
    render(<Home />);
    await waitFor(() => expect(screen.getByText("inv.pdf")).toBeTruthy());
    fireEvent.click(screen.getByText("inv.pdf"));
    await waitFor(() => expect(screen.getByRole("button", { name: /reopen for review/i })).toBeTruthy());
    expect(screen.queryByRole("button", { name: /^approve$/i })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /reopen for review/i }));
    // confirm dialog
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /^reopen$/i }));
    await waitFor(() =>
      expect(apiMock.mock.calls.some(c => c[1]?.body && String(c[1].body).includes('"action":"reopen"'))).toBe(true));
  });
});
