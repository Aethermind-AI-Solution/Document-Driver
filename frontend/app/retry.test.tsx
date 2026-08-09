// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import Home from "./page";

vi.mock("../lib/api", () => ({
  api: vi.fn(async (path: string) => {
    if (path === "/auth/me") return { role: "admin", email: "a@b.co" };
    if (path === "/schemas") return [];
    if (path === "/documents") return [{ id: 5, filename: "bad.pdf", document_type: "invoice",
      status: "error", upload_date: "2026-08-09", review_required: false }];
    return {};
  }),
  me: vi.fn(async () => ({ role: "admin", email: "a@b.co" })),
  pollDocument: vi.fn(), downloadFile: vi.fn(), TERMINAL: ["processed", "review_required", "error"],
}));

describe("retry control", () => {
  beforeEach(() => vi.clearAllMocks());
  it("shows Retry on an errored document", async () => {
    render(<Home />);
    await waitFor(() => expect(screen.getByRole("button", { name: /retry/i })).toBeTruthy());
  });
});
