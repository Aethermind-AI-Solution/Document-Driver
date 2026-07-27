// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import Home from "./page";

const doc = {
  id: 5, filename: "inv.pdf", document_type: "invoice", status: "processed",
  confidence: 0.95, upload_date: "2026-07-27T00:00:00", review_required: false,
  fields: [
    { id: 1, field_name: "total", field_value: "78,880", confidence: 0.95, validated: true, grounded: "grounded", source_quote: "Grand Total 78,880" },
    { id: 2, field_name: "line_items", confidence: 0.95, validated: true, grounded: "grounded", source_quote: null,
      field_value: JSON.stringify([{ description: "Scanner", quantity: "2", amount: "37,000" }, { description: "Printer", quantity: "1", amount: "24,000" }]) },
  ], audit: [],
};

vi.mock("../lib/api", () => ({
  getToken: () => "", setToken: () => {}, clearToken: () => {}, downloadFile: vi.fn(),
  api: vi.fn(async (path: string) => {
    if (path === "/schemas") return [{ key: "invoice", name: "Invoice", fields: [] }];
    if (path === "/documents") return [doc];
    if (path.startsWith("/document/")) return doc;
    return {};
  }),
}));

beforeEach(() => cleanup());

describe("line-items table", () => {
  it("renders an array field as a table and a scalar field as an input", async () => {
    render(<Home />);
    fireEvent.click(await screen.findByText("inv.pdf"));
    await screen.findByDisplayValue("78,880");                 // scalar → input
    expect(screen.getByText("Scanner")).toBeTruthy();          // array → table cell
    expect(screen.getByText("Printer")).toBeTruthy();
    expect(screen.getByText("description")).toBeTruthy();       // column header (raw key)
  });
});
