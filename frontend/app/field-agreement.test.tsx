// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import Home from "./page";

vi.mock("../lib/api", () => ({
  api: vi.fn(async (path: string) => {
    if (path === "/auth/me") return { role: "admin", email: "a@b.co" };
    if (path === "/schemas") return [];
    if (path.startsWith("/documents/stats")) return { total: 4, review_required: 1, avg_processing_time: 1, field_agreement_rate: 0.92 };
    if (path.startsWith("/documents")) return { items: [], total: 0 };
    return {};
  }),
  me: vi.fn(async () => ({ role: "admin", email: "a@b.co" })),
  pollDocument: vi.fn(), downloadFile: vi.fn(), TERMINAL: ["processed", "review_required", "error"],
}));

describe("field agreement tile", () => {
  beforeEach(() => vi.clearAllMocks());
  it("renders the 'Fields accepted as-is' tile from stats", async () => {
    render(<Home />);
    await waitFor(() => expect(screen.getByText("Fields accepted as-is")).toBeTruthy());
    await waitFor(() => expect(screen.getByText("92%")).toBeTruthy());
  });
});
