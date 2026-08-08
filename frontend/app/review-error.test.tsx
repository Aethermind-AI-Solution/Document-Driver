// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import Home from "./page";

const processedDoc = {
  id: 7,
  filename: "bad.pdf",
  document_type: "invoice",
  status: "processed",
  confidence: 0.7,
  upload_date: "2026-07-24T00:00:00",
  review_required: true,
  fields: [{ id: 1, field_name: "total", field_value: "100", confidence: 0.7, validated: true }],
  audit: [],
};

vi.mock("../lib/api", () => ({
  getToken: () => "",
  setToken: () => {},
  clearToken: () => {},
  downloadFile: vi.fn(),
  me: vi.fn(async () => ({ id: 1, email: "a@b.co", role: "admin", is_active: true })),
  api: vi.fn(async (path: string, init?: any) => {
    const method = init?.method || "GET";
    if (path === "/schemas") return [{ key: "invoice", name: "Invoice", fields: [] }];
    if (path === "/documents") return [{ ...processedDoc }];
    if (path.startsWith("/document/") && method === "GET") return { ...processedDoc };
    if (path.startsWith("/document/") && method === "PUT") throw new Error("Processing failed: boom");
    return {};
  }),
}));

beforeEach(() => cleanup());

describe("a failed review action recovers the panel", () => {
  it("re-enables the button, shows the error, and keeps the panel open", async () => {
    render(<Home />);
    fireEvent.click(await screen.findByText("bad.pdf"));
    const approve = await screen.findByRole("button", { name: /^approve$/i });
    fireEvent.click(approve);
    // The error surfaces...
    await waitFor(() => expect(screen.getByText(/Processing failed: boom/)).toBeTruthy());
    // ...the button is no longer stuck on "Saving…" (recovered to "Approve")...
    expect(screen.getByRole("button", { name: /^approve$/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /saving/i })).toBeNull();
    // ...and the panel stays open (field still visible; not closed).
    expect(screen.getByDisplayValue("100")).toBeTruthy();
  });
});
