// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import SuggestedSchemasPage from "./schemas/suggested/page";

vi.mock("../lib/api", () => ({
  me: vi.fn(async () => ({ role: "admin" })),
  api: vi.fn(async (path: string) => {
    if (path === "/schemas/suggested")
      return [{ id: 1, key: "manifest", name: "Manifest",
                fields: [{ name: "carrier", label: "Carrier", type: "string", required: false }],
                origin_document_id: 7, created_at: null }];
    return {};
  }),
}));

describe("SuggestedSchemasPage", () => {
  beforeEach(() => vi.clearAllMocks());
  it("lists suggested schema drafts", async () => {
    render(<SuggestedSchemasPage />);
    await waitFor(() => expect(screen.getByText(/Manifest/)).toBeTruthy());
    expect(screen.getByText(/from doc #7/)).toBeTruthy();
  });
});
