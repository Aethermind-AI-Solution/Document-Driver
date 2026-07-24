// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import Home from "./page";

let gated = true;

vi.mock("../lib/api", () => ({
  getToken: () => "",
  setToken: vi.fn(() => { gated = false; }),   // "logging in" opens the gate
  clearToken: vi.fn(),
  downloadFile: vi.fn(),
  api: vi.fn(async (path: string) => {
    if (gated) { const e: any = new Error("Unauthorized"); e.status = 401; throw e; }
    if (path === "/schemas") return [{ key: "invoice", name: "Invoice", fields: [] }];
    if (path === "/documents") return [];
    return {};
  }),
}));

beforeEach(() => { gated = true; cleanup(); });

describe("demo access gate", () => {
  it("shows the login card on 401 and unlocks after entering the password", async () => {
    render(<Home />);
    await screen.findByText(/demo access password/i);
    fireEvent.change(screen.getByPlaceholderText(/password/i), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: /enter/i }));
    await waitFor(() => expect(screen.getByText("Upload a document")).toBeTruthy());
  });
});
