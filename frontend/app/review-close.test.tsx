// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import Home from "./page";

const processedDoc = {
  id: 12,
  filename: "inv.pdf",
  document_type: "invoice",
  status: "processed",
  confidence: 0.7,
  upload_date: "2026-07-23T00:00:00",
  review_required: true,
  fields: [{ id: 1, field_name: "total", field_value: "100", confidence: 0.7, validated: true }],
  audit: [],
};

// Mirror the real backend: after a successful PUT approve, the document's
// status becomes "approved", so a subsequent /documents refresh reflects it.
let approved = false;

vi.mock("../lib/api", () => ({
  getToken: () => "",
  setToken: () => {},
  clearToken: () => {},
  api: vi.fn(async (path: string, init?: any) => {
    const method = init?.method || "GET";
    if (path === "/schemas") return [{ key: "invoice", name: "Invoice", fields: [] }];
    if (path === "/documents") return [{ ...processedDoc, status: approved ? "approved" : "processed" }];
    if (path.startsWith("/document/") && method === "GET") return { ...processedDoc };
    if (path.startsWith("/document/") && method === "PUT") { approved = true; return { ...processedDoc, status: "approved", review_required: false }; }
    return {};
  }),
}));

beforeEach(() => { approved = false; cleanup(); });

describe("approve closes the review panel", () => {
  it("turns the queue pill green AND resets the panel to the placeholder", async () => {
    render(<Home />);
    // Open the processed document from the queue.
    fireEvent.click(await screen.findByText("inv.pdf"));
    // The review panel now shows the editable field value.
    await screen.findByDisplayValue("100");
    // Approve it.
    fireEvent.click(screen.getByRole("button", { name: /^approve$/i }));
    // The queue pill reflects approval (matches the user's observation)...
    await waitFor(() => expect(screen.getByText("approved")).toBeTruthy());
    // ...AND the panel resets to the placeholder and stops showing the field.
    expect(screen.getByText("Select a processed document")).toBeTruthy();
    expect(screen.queryByDisplayValue("100")).toBeNull();
  });
});
