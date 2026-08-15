// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import Home from "./page";

const doc = {
  id: 9, filename: "inv.pdf", document_type: "invoice", status: "review_required",
  confidence: 0.9, upload_date: "2026-08-15", review_required: true, anomalies: [],
  fields: [{ id: 1, field_name: "total", field_value: "100", confidence: 0.95, validated: true, grounded: "grounded" }],
  audit: [
    { action: "Uploaded", timestamp: "2026-08-15T10:00:00", details: "inv.pdf", actor_email: "a@b.co" },
    { action: "Approved", timestamp: "2026-08-15T10:05:00", details: "Human review completed", actor_email: "a@b.co" },
  ],
};
vi.mock("../lib/api", () => ({
  me: vi.fn(async () => ({ role: "admin", email: "a@b.co" })),
  api: vi.fn(async (path: string) => {
    if (path === "/schemas") return [{ key: "invoice", name: "Invoice", fields: [] }];
    if (path.startsWith("/documents/stats")) return { total: 1, review_required: 1, avg_processing_time: 1 };
    if (path.startsWith("/documents")) return { items: [doc], total: 1 };
    if (path.startsWith("/document/")) return doc;
    return {};
  }),
  pollDocument: vi.fn(), downloadFile: vi.fn(), TERMINAL: ["processed", "review_required", "error"],
}));

describe("audit trail", () => {
  beforeEach(() => vi.clearAllMocks());
  it("shows audit entries for the selected document", async () => {
    render(<Home />);
    await waitFor(() => expect(screen.getByText("inv.pdf")).toBeTruthy());
    fireEvent.click(screen.getByText("inv.pdf"));
    await waitFor(() => expect(screen.getByRole("button", { name: /activity/i })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /activity/i }));
    await waitFor(() => expect(screen.getByText(/Human review completed/)).toBeTruthy());
  });
});
