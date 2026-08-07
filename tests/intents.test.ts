import { describe, expect, it } from "vitest";
import { detectIntent, detectPeriod, extractName, isConfirmation, isNegative } from "../src/domain/intents.js";

describe("intent parsing", () => {
  it("supports Devanagari user replies", () => {
    expect(detectIntent("कृपया मेरी appointment रद्द कर दीजिए")).toBe("CANCEL");
    expect(detectPeriod("सुबह")).toBe("MORNING");
    expect(detectPeriod("शाम")).toBe("EVENING");
    expect(isConfirmation("हाँ")).toBe(true);
    expect(isNegative("नहीं")).toBe(true);
  });

  it("preserves non-Latin names and formats Latin names safely", () => {
    expect(extractName("राहुल शर्मा")).toBe("राहुल शर्मा");
    expect(extractName("anne-marie o'connor")).toBe("Anne-Marie O'Connor");
  });
});
