import { describe, it, expect } from "vitest";
import { pollDocument, TERMINAL } from "./api";

describe("pollDocument", () => {
  it("resolves when status becomes terminal", async () => {
    let n = 0;
    const fetchDoc = async () => ({ id: 1, status: n++ === 0 ? "processing" : "processed" });
    const doc = await pollDocument(1, { intervalMs: 1, fetchDoc });
    expect(doc.status).toBe("processed");
  });

  it("throws on timeout while still processing", async () => {
    const fetchDoc = async () => ({ id: 1, status: "processing" });
    await expect(pollDocument(1, { intervalMs: 1, timeoutMs: 5, fetchDoc })).rejects.toThrow();
  });

  it("TERMINAL lists the three end states", () => {
    expect(TERMINAL).toEqual(["processed", "review_required", "error"]);
  });
});
