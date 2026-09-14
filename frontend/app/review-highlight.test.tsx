import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { renderAnomaly } from "../lib/anomaly";

describe("anomaly rendering", () => {
  it("renders a string anomaly as-is", () => {
    expect(renderAnomaly("Subtotal != total")).toBe("Subtotal != total");
  });
  it("renders a dict anomaly's message", () => {
    expect(renderAnomaly({ type: "duplicate_invoice", message: "Possible duplicate of a.png", duplicate_of: 1 }))
      .toBe("Possible duplicate of a.png");
  });
});
