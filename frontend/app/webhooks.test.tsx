// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import WebhooksPage from "./webhooks/page";

vi.mock("../lib/api", () => ({
  api: vi.fn(async (path: string) => {
    if (path === "/webhooks") return [{ id: 1, document_type: "invoice", url: "https://h/x", active: true, has_secret: true }];
    return {};
  }),
}));

describe("WebhooksPage", () => {
  beforeEach(() => vi.clearAllMocks());
  it("lists configured webhooks", async () => {
    render(<WebhooksPage />);
    await waitFor(() => expect(screen.getByText(/invoice → https:\/\/h\/x/)).toBeTruthy());
  });
});
