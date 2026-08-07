import dayjs from "dayjs";
import { describe, expect, it } from "vitest";
import { InMemoryRepository } from "../src/repositories/in-memory.js";
import { BookingCoreService } from "../src/services/booking-core-service.js";

function tomorrowDate() {
  return dayjs().add(1, "day").format("DD-MM-YYYY");
}

describe("BookingCoreService", () => {
  it("enforces deterministic gate order and tool chaining", async () => {
    const repo = new InMemoryRepository();
    const service = new BookingCoreService(repo);
    const chatId = "1001";

    let response = await service.advanceSession({
      chatId,
      messageId: "m1",
      userText: "Rahul Sharma",
      correlationId: "corr-1"
    });
    expect(response.state).toBe("NEED_MOBILE");
    expect(response.next_action.kind).toBe("ASK_USER");

    response = await service.advanceSession({
      chatId,
      messageId: "m2",
      userText: "9876543210",
      correlationId: "corr-2"
    });
    expect(response.state).toBe("NEED_DATE");

    response = await service.advanceSession({
      chatId,
      messageId: "m3",
      userText: tomorrowDate(),
      correlationId: "corr-3"
    });
    expect(response.state).toBe("NEED_PERIOD");

    response = await service.advanceSession({
      chatId,
      messageId: "m4",
      userText: "evening",
      correlationId: "corr-4"
    });
    expect(response.next_action.kind).toBe("RUN_TOOL");
    expect(response.next_action.tool).toBe("CALENDAR_CHECK");
    expect(response.tool_request?.correlation_id).toBe("corr-4");

    let toolResponse = await service.handleToolResult({
      correlationId: "corr-4",
      toolName: "CALENDAR_CHECK",
      toolOutput: { success: true, busy_slots_utc: [] }
    });
    expect(toolResponse.next_action.kind).toBe("ASK_USER");
    expect(toolResponse.reply_text).toContain("Please choose from the following available slots");

    response = await service.advanceSession({
      chatId,
      messageId: "m5",
      userText: "06:30 PM",
      correlationId: "corr-5"
    });
    expect(response.state).toBe("NEED_CONFIRMATION");
    expect(response.next_action.kind).toBe("ASK_USER");

    response = await service.advanceSession({
      chatId,
      messageId: "m6",
      userText: "yes",
      correlationId: "corr-6"
    });
    expect(response.next_action.kind).toBe("RUN_TOOL");
    expect(response.next_action.tool).toBe("CALENDAR_CREATE");

    toolResponse = await service.handleToolResult({
      correlationId: "corr-6",
      toolName: "CALENDAR_CREATE",
      toolOutput: { success: true, event_id: "evt_123" }
    });

    expect(toolResponse.next_action.kind).toBe("RUN_TOOL");
    expect(toolResponse.next_action.tool).toBe("SHEET_UPSERT");
    expect(toolResponse.tool_request?.tool).toBe("SHEET_UPSERT");

    toolResponse = await service.handleToolResult({
      correlationId: "corr-6",
      toolName: "SHEET_UPSERT",
      toolOutput: { success: true }
    });

    expect(toolResponse.next_action.kind).toBe("DONE");
    expect(toolResponse.reply_text).toContain("Your appointment is confirmed");
  });

  it("rejects same-day booking attempts", async () => {
    const repo = new InMemoryRepository();
    const service = new BookingCoreService(repo);
    const chatId = "1002";

    await service.advanceSession({
      chatId,
      messageId: "a1",
      userText: "Priya",
      correlationId: "a-c1"
    });

    await service.advanceSession({
      chatId,
      messageId: "a2",
      userText: "9999988888",
      correlationId: "a-c2"
    });

    const response = await service.advanceSession({
      chatId,
      messageId: "a3",
      userText: dayjs().format("DD-MM-YYYY"),
      correlationId: "a-c3"
    });

    expect(response.next_action.kind).toBe("ASK_USER");
    expect(response.reply_text).toContain("do not accept same-day");
  });

  it("deduplicates inbound telegram message ingestion", async () => {
    const repo = new InMemoryRepository();
    const service = new BookingCoreService(repo);

    const first = await service.ingestInbound({
      channel: "telegram",
      chatId: "1003",
      messageId: "msg-1",
      text: "hello",
      rawPayload: { t: 1 },
      correlationId: "d-c1"
    });

    const second = await service.ingestInbound({
      channel: "telegram",
      chatId: "1003",
      messageId: "msg-1",
      text: "hello",
      rawPayload: { t: 1 },
      correlationId: "d-c2"
    });

    expect(first).toEqual({ accepted: true, duplicate: false });
    expect(second).toEqual({ accepted: false, duplicate: true });
  });

  it("queues fallback on tool failure", async () => {
    const repo = new InMemoryRepository();
    const service = new BookingCoreService(repo);
    const chatId = "1004";

    await service.advanceSession({
      chatId,
      messageId: "q1",
      userText: "Ravi",
      correlationId: "q-c1"
    });
    await service.advanceSession({
      chatId,
      messageId: "q2",
      userText: "9123456789",
      correlationId: "q-c2"
    });
    await service.advanceSession({
      chatId,
      messageId: "q3",
      userText: tomorrowDate(),
      correlationId: "q-c3"
    });

    const runTool = await service.advanceSession({
      chatId,
      messageId: "q4",
      userText: "evening",
      correlationId: "q-c4"
    });

    expect(runTool.next_action.kind).toBe("RUN_TOOL");

    const result = await service.handleToolResult({
      correlationId: "q-c4",
      toolName: "CALENDAR_CHECK",
      toolOutput: { success: false, error: "calendar timeout" }
    });

    expect(result.next_action.kind).toBe("ASK_USER");
    expect(result.reply_text).toContain("temporary system delay");
    const snapshot = repo.snapshot();
    expect(snapshot.outbox.length).toBeGreaterThan(0);
  });

  it("accepts Hindi-script replies through booking and cancellation flows", async () => {
    const repo = new InMemoryRepository();
    const service = new BookingCoreService(repo);
    const chatId = "1005";

    let response = await service.advanceSession({
      chatId,
      messageId: "h1",
      userText: "राहुल शर्मा",
      correlationId: "h-c1"
    });
    expect(response.state).toBe("NEED_MOBILE");

    response = await service.advanceSession({
      chatId,
      messageId: "h2",
      userText: "9876543210",
      correlationId: "h-c2"
    });
    expect(response.state).toBe("NEED_DATE");

    response = await service.advanceSession({
      chatId,
      messageId: "h3",
      userText: tomorrowDate(),
      correlationId: "h-c3"
    });
    expect(response.state).toBe("NEED_PERIOD");

    response = await service.advanceSession({
      chatId,
      messageId: "h4",
      userText: "शाम",
      correlationId: "h-c4"
    });
    expect(response.next_action.kind).toBe("RUN_TOOL");
    expect(response.next_action.tool).toBe("CALENDAR_CHECK");

    let toolResponse = await service.handleToolResult({
      correlationId: "h-c4",
      toolName: "CALENDAR_CHECK",
      toolOutput: { success: true, busy_slots_utc: [] }
    });
    expect(toolResponse.next_action.kind).toBe("ASK_USER");
    expect(toolResponse.state).toBe("NEED_SLOT_SELECTION");

    response = await service.advanceSession({
      chatId,
      messageId: "h5",
      userText: "06:30 PM",
      correlationId: "h-c5"
    });
    expect(response.state).toBe("NEED_CONFIRMATION");

    response = await service.advanceSession({
      chatId,
      messageId: "h6",
      userText: "हाँ",
      correlationId: "h-c6"
    });
    expect(response.next_action.kind).toBe("RUN_TOOL");
    expect(response.next_action.tool).toBe("CALENDAR_CREATE");

    toolResponse = await service.handleToolResult({
      correlationId: "h-c6",
      toolName: "CALENDAR_CREATE",
      toolOutput: { success: true, event_id: "evt_hi_123" }
    });
    expect(toolResponse.next_action.kind).toBe("RUN_TOOL");
    expect(toolResponse.next_action.tool).toBe("SHEET_UPSERT");

    toolResponse = await service.handleToolResult({
      correlationId: "h-c6",
      toolName: "SHEET_UPSERT",
      toolOutput: { success: true }
    });
    expect(toolResponse.next_action.kind).toBe("DONE");
    expect(toolResponse.reply_text).toContain("appointment confirm ho gaya");

    response = await service.advanceSession({
      chatId,
      messageId: "h7",
      userText: "रद्द",
      correlationId: "h-c7"
    });
    expect(response.next_action.kind).toBe("RUN_TOOL");
    expect(response.next_action.tool).toBe("CALENDAR_DELETE");
  });
});
