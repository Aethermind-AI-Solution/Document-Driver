// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import Home from "./page";

const items = [
  { id: 1, filename: "auto-doc.pdf", document_type: "invoice", status: "approved", upload_date: "2026-08-15", review_required: false, auto_approved: true },
  { id: 2, filename: "manual-doc.pdf", document_type: "invoice", status: "processed", upload_date: "2026-08-15", review_required: true, auto_approved: false },
  { id: 3, filename: "other-doc.pdf", document_type: "invoice", status: "processed", upload_date: "2026-08-15", review_required: false },
];
const apiMock = vi.fn(async (path: string) => {
  if (path === "/auth/me") return { role: "admin", email: "a@b.co" };
  if (path === "/schemas") return [];
  if (path.startsWith("/documents/stats")) return { total: 3, review_required: 0, avg_processing_time: 1, auto_approved: 2, auto_approved_reopen_rate: 0.5, webhook_failed: 1 };
  if (path.startsWith("/documents")) return { items, total: 3 };
  return {};
});
vi.mock("../lib/api", () => ({
  api: (p: string, i?: any) => apiMock(p, i),
  me: vi.fn(async () => ({ role: "admin", email: "a@b.co" })),
  pollDocument: vi.fn(), downloadFile: vi.fn(), TERMINAL: ["processed", "review_required", "error"],
}));

describe("auto-approve surfacing", () => {
  beforeEach(() => { vi.clearAllMocks(); cleanup(); });

  it("shows the auto indicator on auto-approved docs and dashboard tile values", async () => {
    render(<Home />);
    await waitFor(() => expect(screen.getByText("auto-doc.pdf")).toBeTruthy());
    const autoDocRow = screen.getByText("auto-doc.pdf").closest("div");
    expect(autoDocRow?.parentElement?.textContent).toMatch(/auto/i);
    const manualDocRow = screen.getByText("manual-doc.pdf").closest("div");
    expect(manualDocRow?.parentElement?.querySelector('[title="Auto-approved"]')).toBeFalsy();

    await waitFor(() => expect(screen.getByText("Auto-approved")).toBeTruthy());
    const autoTile = screen.getByText("Auto-approved").closest("div");
    expect(autoTile?.textContent).toMatch(/2/);

    expect(screen.getByText("Auto-reopen rate")).toBeTruthy();
    expect(screen.getByText("50%")).toBeTruthy();

    expect(screen.getByText("Webhook failures")).toBeTruthy();
  });
});
