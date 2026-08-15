// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import Home from "./page";

// Render-based guard: assert the actual upload affordance (and the admin-only
// "Users" link) are present/absent in the DOM per role, not just that the
// underlying canUpload/canReview predicates return the right booleans
// (that's already covered by lib/auth.test.ts).
let role: "admin" | "reviewer" | "viewer" = "viewer";

vi.mock("../lib/api", () => ({
  getToken: () => "",
  setToken: vi.fn(),
  clearToken: vi.fn(),
  downloadFile: vi.fn(),
  me: vi.fn(async () => ({ id: 1, email: "u@x.co", role, is_active: true })),
  api: vi.fn(async (path: string) => {
    if (path === "/schemas") return [{ key: "invoice", name: "Invoice", fields: [] }];
    if (path.startsWith("/documents/stats")) return { total: 0, review_required: 0, avg_processing_time: null };
    if (path.startsWith("/documents")) return { items: [], total: 0 };
    return {};
  }),
}));

beforeEach(() => cleanup());

describe("role-gated main page", () => {
  it("hides the upload control and the Users link for a viewer", async () => {
    role = "viewer";
    render(<Home />);
    await screen.findByText("Aethermind Document Intelligence");
    await waitFor(() => expect(screen.queryByText(/drop a document or click to browse/i)).toBeNull());
    expect(screen.queryByRole("link", { name: /users/i })).toBeNull();
  });

  it("shows the upload control for a reviewer, but not the Users link", async () => {
    role = "reviewer";
    render(<Home />);
    await waitFor(() => expect(screen.getByText(/drop a document or click to browse/i)).toBeTruthy());
    expect(screen.queryByRole("link", { name: /users/i })).toBeNull();
  });

  it("shows the upload control and the Users link for an admin", async () => {
    role = "admin";
    render(<Home />);
    await waitFor(() => expect(screen.getByText(/drop a document or click to browse/i)).toBeTruthy());
    expect(screen.getByRole("link", { name: /users/i })).toBeTruthy();
  });
});
