import { describe, it, expect } from "vitest";
import { canUpload, canReview, isAdmin } from "./auth";

describe("role capabilities", () => {
  it("viewer can neither upload nor review", () => {
    expect(canUpload("viewer")).toBe(false);
    expect(canReview("viewer")).toBe(false);
  });
  it("reviewer can upload and review but is not admin", () => {
    expect(canUpload("reviewer")).toBe(true);
    expect(canReview("reviewer")).toBe(true);
    expect(isAdmin("reviewer")).toBe(false);
  });
  it("admin can do everything", () => {
    expect(canUpload("admin")).toBe(true);
    expect(isAdmin("admin")).toBe(true);
  });
});
