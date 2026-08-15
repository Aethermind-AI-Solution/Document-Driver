// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import WebhooksPage from "./webhooks/page";

const apiMock = vi.fn(async (path: string) => {
  if (path === "/webhooks") return [{ id: 1, document_type: "invoice", url: "https://h/x", active: true, has_secret: false }];
  return {};
});
vi.mock("../lib/api", () => ({ api: (p: string, i?: any) => apiMock(p, i) }));

describe("destructive-action confirmation", () => {
  beforeEach(() => vi.clearAllMocks());
  it("does not DELETE until the dialog is confirmed", async () => {
    render(<WebhooksPage />);
    await waitFor(() => expect(screen.getByText(/invoice → https:\/\/h\/x/)).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /delete/i }));
    // dialog open, no DELETE yet
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(apiMock.mock.calls.some(c => c[1]?.method === "DELETE")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: /^confirm$/i }));
    await waitFor(() =>
      expect(apiMock.mock.calls.some(c => c[1]?.method === "DELETE")).toBe(true));
  });
});
