// frontend/app/redaction.test.tsx
import { describe, it, expect } from "vitest";
import { maskValue } from "../lib/redact";

describe("maskValue", () => {
  it("masks a PII value when hidden", () => { expect(maskValue("John", true, true)).toBe("••••"); });
  it("shows a PII value when not hidden", () => { expect(maskValue("John", true, false)).toBe("John"); });
  it("never masks a non-PII value", () => { expect(maskValue("100", false, true)).toBe("100"); });
});
