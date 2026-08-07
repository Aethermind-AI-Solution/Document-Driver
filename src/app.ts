import Fastify, { FastifyInstance } from "fastify";
import { Pool } from "pg";
import { v4 as uuidv4 } from "uuid";
import { z } from "zod";
import { config } from "./lib/config.js";
import { InMemoryRepository } from "./repositories/in-memory.js";
import { PostgresRepository } from "./repositories/postgres.js";
import { Repository } from "./repositories/repository.js";
import { BookingCoreService } from "./services/booking-core-service.js";

const inboundTelegramSchema = z.object({
  chat_id: z.string(),
  message_id: z.union([z.string(), z.number()]).transform((v) => String(v)),
  text: z.string().optional(),
  timestamp: z.string().optional(),
  raw_payload: z.unknown().optional()
});

const sessionAdvanceSchema = z.object({
  chat_id: z.string(),
  message_id: z.union([z.string(), z.number()]).transform((v) => String(v)),
  user_text: z.string()
});

const toolResultSchema = z.object({
  correlation_id: z.string(),
  tool_name: z.enum(["CALENDAR_CHECK", "CALENDAR_CREATE", "CALENDAR_DELETE", "SHEET_UPSERT"]),
  tool_output: z.record(z.unknown())
});

const confirmSchema = z.object({
  session_id: z.string(),
  idempotency_key: z.string().min(10)
});

const cancelOrRescheduleSchema = z.object({
  appointment_id: z.string().optional(),
  chat_id: z.string().optional(),
  date: z.string().optional(),
  time: z.string().optional()
});

const reminderMessageSchema = z.object({
  patient_name: z.string().min(1),
  appointment_time: z.string().min(1),
  chat_id: z.string().min(1),
  language: z.enum(["en", "hi"]).optional()
});

const outboxMarkSchema = z.object({
  status: z.enum(["queued", "sent", "failed"]),
  retry_count: z.coerce.number().int().min(0),
  next_retry_at: z.string().optional(),
  last_error: z.string().optional()
});

export interface BuildAppOptions {
  repo?: Repository;
}

export async function buildApp(options: BuildAppOptions = {}): Promise<FastifyInstance> {
  const app = Fastify({
    logger: {
      level: config.LOG_LEVEL
    }
  });

  let pool: Pool | null = null;
  const repo = options.repo ?? (() => {
    if (config.NODE_ENV === "test") {
      return new InMemoryRepository();
    }
    pool = new Pool({ connectionString: config.DATABASE_URL });
    return new PostgresRepository(pool);
  })();

  const service = new BookingCoreService(repo);

  app.get("/health", async () => {
    return {
      status: "ok",
      service: "booking-core-api",
      time: new Date().toISOString()
    };
  });

  app.post("/v1/inbound/telegram", async (request, reply) => {
    const parsed = inboundTelegramSchema.safeParse(request.body);
    if (!parsed.success) {
      return reply.status(400).send({ error: "Invalid request", details: parsed.error.flatten() });
    }

    const correlationId = String(request.headers["x-correlation-id"] ?? uuidv4());
    const body = parsed.data;

    const ingestResult = await service.ingestInbound({
      channel: "telegram",
      chatId: body.chat_id,
      messageId: body.message_id,
      text: body.text,
      rawPayload: body.raw_payload ?? parsed.data,
      correlationId
    });

    return reply.send({
      correlation_id: correlationId,
      accepted: ingestResult.accepted,
      duplicate: ingestResult.duplicate
    });
  });

  app.post("/v1/session/advance", async (request, reply) => {
    const parsed = sessionAdvanceSchema.safeParse(request.body);
    if (!parsed.success) {
      return reply.status(400).send({ error: "Invalid request", details: parsed.error.flatten() });
    }

    const correlationId = String(request.headers["x-correlation-id"] ?? uuidv4());
    const body = parsed.data;

    const response = await service.advanceSession({
      chatId: body.chat_id,
      messageId: body.message_id,
      userText: body.user_text,
      correlationId
    });

    return reply.send(response);
  });

  app.post("/v1/tools/result", async (request, reply) => {
    const parsed = toolResultSchema.safeParse(request.body);
    if (!parsed.success) {
      return reply.status(400).send({ error: "Invalid request", details: parsed.error.flatten() });
    }

    const body = parsed.data;
    const response = await service.handleToolResult({
      correlationId: body.correlation_id,
      toolName: body.tool_name,
      toolOutput: body.tool_output
    });

    return reply.send(response);
  });

  app.post("/v1/appointments/confirm", async (request, reply) => {
    const parsed = confirmSchema.safeParse(request.body);
    if (!parsed.success) {
      return reply.status(400).send({ error: "Invalid request", details: parsed.error.flatten() });
    }

    try {
      const response = await service.confirmAppointment({
        sessionId: parsed.data.session_id,
        idempotencyKey: parsed.data.idempotency_key
      });
      return reply.send(response);
    } catch (error) {
      return reply.status(400).send({ error: error instanceof Error ? error.message : "Unable to confirm appointment" });
    }
  });

  app.post("/v1/appointments/cancel", async (request, reply) => {
    const parsed = cancelOrRescheduleSchema.safeParse(request.body);
    if (!parsed.success) {
      return reply.status(400).send({ error: "Invalid request", details: parsed.error.flatten() });
    }

    try {
      const response = await service.cancelAppointment(parsed.data);
      return reply.send(response);
    } catch (error) {
      return reply.status(404).send({ error: error instanceof Error ? error.message : "Unable to cancel appointment" });
    }
  });

  app.post("/v1/appointments/reschedule", async (request, reply) => {
    const parsed = cancelOrRescheduleSchema.safeParse(request.body);
    if (!parsed.success) {
      return reply.status(400).send({ error: "Invalid request", details: parsed.error.flatten() });
    }

    try {
      const response = await service.rescheduleAppointment(parsed.data);
      return reply.send(response);
    } catch (error) {
      return reply.status(404).send({ error: error instanceof Error ? error.message : "Unable to reschedule appointment" });
    }
  });

  app.get("/v1/outbox/pending", async (request, reply) => {
    const query = z
      .object({
        limit: z.coerce.number().int().min(1).max(200).default(50)
      })
      .safeParse(request.query);

    if (!query.success) {
      return reply.status(400).send({ error: "Invalid query", details: query.error.flatten() });
    }

    const messages = await service.getDueOutbox(query.data.limit);
    return reply.send({ count: messages.length, items: messages });
  });

  app.get("/v1/outbox/failed", async (request, reply) => {
    const query = z
      .object({
        limit: z.coerce.number().int().min(1).max(200).default(50)
      })
      .safeParse(request.query);

    if (!query.success) {
      return reply.status(400).send({ error: "Invalid query", details: query.error.flatten() });
    }

    const messages = await service.getFailedOutbox(query.data.limit);
    return reply.send({ count: messages.length, items: messages });
  });

  app.post("/v1/outbox/:id/mark", async (request, reply) => {
    const params = z.object({ id: z.string() }).safeParse(request.params);
    if (!params.success) {
      return reply.status(400).send({ error: "Invalid params", details: params.error.flatten() });
    }

    const body = outboxMarkSchema.safeParse(request.body);
    if (!body.success) {
      return reply.status(400).send({ error: "Invalid request", details: body.error.flatten() });
    }

    await service.markOutboxMessage(
      params.data.id,
      body.data.status,
      body.data.retry_count,
      body.data.next_retry_at ?? null,
      body.data.last_error ?? null
    );

    return reply.send({ ok: true });
  });

  app.post("/v1/reminders/render", async (request, reply) => {
    const parsed = reminderMessageSchema.safeParse(request.body);
    if (!parsed.success) {
      return reply.status(400).send({ error: "Invalid request", details: parsed.error.flatten() });
    }

    const payload = service.buildReminderMessage({
      patientName: parsed.data.patient_name,
      appointmentTime: parsed.data.appointment_time,
      chatId: parsed.data.chat_id,
      language: parsed.data.language
    });

    return reply.send(payload);
  });

  app.addHook("onClose", async () => {
    if (pool) {
      await pool.end();
    }
  });

  return app;
}
