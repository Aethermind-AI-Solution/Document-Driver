import { describe, expect, it } from "vitest";
import { buildApp } from "../src/app.js";
import { InMemoryRepository } from "../src/repositories/in-memory.js";
describe("Booking Core API", () => {
    it("handles inbound dedup and session advance", async () => {
        const repo = new InMemoryRepository();
        const app = await buildApp({ repo });
        const first = await app.inject({
            method: "POST",
            url: "/v1/inbound/telegram",
            payload: {
                chat_id: "2001",
                message_id: "1",
                text: "hello"
            }
        });
        expect(first.statusCode).toBe(200);
        expect(first.json().accepted).toBe(true);
        const second = await app.inject({
            method: "POST",
            url: "/v1/inbound/telegram",
            payload: {
                chat_id: "2001",
                message_id: "1",
                text: "hello"
            }
        });
        expect(second.statusCode).toBe(200);
        expect(second.json().duplicate).toBe(true);
        const advance = await app.inject({
            method: "POST",
            url: "/v1/session/advance",
            payload: {
                chat_id: "2001",
                message_id: "2",
                user_text: "Aman Kumar"
            }
        });
        expect(advance.statusCode).toBe(200);
        expect(advance.json().state).toBe("NEED_MOBILE");
        await app.close();
    });
    it("renders deterministic reminder text", async () => {
        const app = await buildApp({ repo: new InMemoryRepository() });
        const response = await app.inject({
            method: "POST",
            url: "/v1/reminders/render",
            payload: {
                patient_name: "Neha Singh",
                appointment_time: "06:40 PM",
                chat_id: "2010",
                language: "en"
            }
        });
        expect(response.statusCode).toBe(200);
        expect(response.json().chat_id).toBe("2010");
        expect(response.json().text).toContain("Neha");
        expect(response.json().text).toContain("06:40 PM");
        await app.close();
    });
    it("returns graceful response when tool result correlation is unknown", async () => {
        const app = await buildApp({ repo: new InMemoryRepository() });
        const response = await app.inject({
            method: "POST",
            url: "/v1/tools/result",
            payload: {
                correlation_id: "unknown-corr",
                tool_name: "CALENDAR_CHECK",
                tool_output: { success: true, busy_slots_utc: [] }
            }
        });
        expect(response.statusCode).toBe(200);
        expect(response.json().reply_text).toContain("Session not found");
        await app.close();
    });
});
