// frontend/app/redaction.test.tsx
import { describe, it, expect } from "vitest";
import { maskValue } from "../lib/redact";

describe("maskValue", () => {
  it("masks a PII value when hidden", () => { expect(maskValue("John", true, true)).toBe("••••"); });
  it("shows a PII value when not hidden", () => { expect(maskValue("John", true, false)).toBe("John"); });
  it("never masks a non-PII value", () => { expect(maskValue("100", false, true)).toBe("100"); });
});

describe("maskValue for table cells (stringified)", () => {
  it("masks a numeric PII cell when hidden", () => { expect(maskValue(String(1234 ?? ""), true, true)).toBe("••••"); });
  it("shows a numeric non-PII cell", () => { expect(maskValue(String(1234 ?? ""), false, true)).toBe("1234"); });
  it("leaves an empty cell unmasked (renders as dash upstream)", () => { expect(maskValue(String(null ?? ""), true, true)).toBe(""); });
});
