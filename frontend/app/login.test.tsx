// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import LoginPage from "./login/page";

vi.mock("../lib/api", () => ({ login: vi.fn().mockResolvedValue({ access_token: "t", user: { role: "admin" } }) }));

describe("LoginPage", () => {
  beforeEach(() => vi.clearAllMocks());
  it("submits email + password", async () => {
    const { login } = await import("../lib/api");
    render(<LoginPage />);
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "a@b.co" } });
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: "password1" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => expect(login).toHaveBeenCalledWith("a@b.co", "password1"));
  });
});
