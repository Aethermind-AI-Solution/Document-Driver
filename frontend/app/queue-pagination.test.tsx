// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import Home from "./page";

const items = Array.from({ length: 20 }, (_, i) => ({
  id: i + 1, filename: `doc${i + 1}.pdf`, document_type: "invoice",
  status: "processed", upload_date: "2026-08-15", review_required: false,
}));
const apiMock = vi.fn(async (path: string) => {
  if (path === "/auth/me") return { role: "admin", email: "a@b.co" };
  if (path === "/schemas") return [];
  if (path.startsWith("/documents/stats")) return { total: 45, review_required: 3, avg_processing_time: 1.5 };
  if (path.startsWith("/documents")) return { items, total: 45 };
  return {};
});
vi.mock("../lib/api", () => ({
  api: (p: string, i?: any) => apiMock(p, i),
  me: vi.fn(async () => ({ role: "admin", email: "a@b.co" })),
  pollDocument: vi.fn(), downloadFile: vi.fn(), TERMINAL: ["processed", "review_required", "error"],
}));

describe("queue pagination", () => {
  beforeEach(() => { vi.clearAllMocks(); cleanup(); });
  it("shows the page range and pages forward", async () => {
    render(<Home />);
    await waitFor(() => expect(screen.getByText(/1–20 of 45/)).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    await waitFor(() =>
      expect(apiMock.mock.calls.some(c => String(c[0]).includes("offset=20"))).toBe(true));
  });
  it("passes the search query to the API", async () => {
    render(<Home />);
    await waitFor(() => expect(screen.getByLabelText(/search documents/i)).toBeTruthy());
    fireEvent.change(screen.getByLabelText(/search documents/i), { target: { value: "invoice42" } });
    await waitFor(() =>
      expect(apiMock.mock.calls.some(c => String(c[0]).includes("q=invoice42"))).toBe(true));
  });
});
