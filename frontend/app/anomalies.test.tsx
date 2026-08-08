// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import Home from "./page";

const doc = {
  id: 9,
  filename: "inv.pdf",
  document_type: "invoice",
  status: "review_required",
  confidence: 0.82,
  upload_date: "2026-07-27T00:00:00",
  review_required: true,
  anomalies: ["Possible duplicate of #3"],
  pipeline_trace: [
    { name: "Classifier", status: "ok", detail: "invoice (0.97)", duration_ms: 120 },
    { name: "Validator", status: "attention", detail: "1 anomaly(ies)", duration_ms: 5 },
  ],
  fields: [
    { id: 1, field_name: "total", field_value: "78,880", confidence: 0.95, validated: true, grounded: "grounded", source_quote: "Grand Total 78,880" },
  ],
  audit: [],
};

vi.mock("../lib/api", () => ({
  getToken: () => "", setToken: () => {}, clearToken: () => {}, downloadFile: vi.fn(),
  me: vi.fn(async () => ({ id: 1, email: "a@b.co", role: "admin", is_active: true })),
  api: vi.fn(async (path: string) => {
    if (path === "/schemas") return [{ key: "invoice", name: "Invoice", fields: [] }];
    if (path === "/documents") return [doc];
    if (path.startsWith("/document/")) return doc;
    return {};
  }),
}));

beforeEach(() => cleanup());

describe("anomaly chips + real pipeline trace", () => {
  it("renders an amber chip for each anomaly on the selected document", async () => {
    render(<Home />);
    fireEvent.click(await screen.findByText("inv.pdf"));
    expect(await screen.findByText("Possible duplicate of #3")).toBeTruthy();
  });

  it("renders the real pipeline trace agent names in the activity panel", async () => {
    render(<Home />);
    fireEvent.click(await screen.findByText("inv.pdf"));
    expect(await screen.findByText(/Classifier/)).toBeTruthy();
    expect(await screen.findByText(/Validator/)).toBeTruthy();
  });
});
