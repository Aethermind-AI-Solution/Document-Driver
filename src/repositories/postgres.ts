import { Pool, QueryResult } from "pg";
import { v4 as uuidv4 } from "uuid";
import { Appointment, Patient, Session, SessionData, SessionState } from "../domain/types.js";
import {
  AuditEventInput,
  CreateAppointmentInput,
  InboundMessageInput,
  OutboxMessageRecord,
  OutboxMessageInput,
  Repository,
  ToolRunInput
} from "./repository.js";

function mapSession(row: Record<string, unknown>): Session {
  return {
    id: String(row.id),
    chatId: String(row.chat_id),
    state: String(row.state) as SessionState,
    data: (row.data as SessionData) ?? {},
    expiresAt: new Date(String(row.expires_at)).toISOString(),
    createdAt: new Date(String(row.created_at)).toISOString(),
    updatedAt: new Date(String(row.updated_at)).toISOString(),
    status: String(row.status) as "active" | "closed"
  };
}

function mapPatient(row: Record<string, unknown>): Patient {
  return {
    id: String(row.id),
    chatId: String(row.telegram_chat_id),
    fullName: String(row.full_name),
    mobile: String(row.mobile),
    createdAt: new Date(String(row.created_at)).toISOString(),
    updatedAt: new Date(String(row.updated_at)).toISOString()
  };
}

function mapAppointment(row: Record<string, unknown>): Appointment {
  return {
    id: String(row.id),
    patientId: String(row.patient_id),
    chatId: String(row.chat_id),
    doctorId: String(row.doctor_id),
    startAt: new Date(String(row.start_at)).toISOString(),
    endAt: new Date(String(row.end_at)).toISOString(),
    status: String(row.status) as Appointment["status"],
    calendarEventId: row.calendar_event_id ? String(row.calendar_event_id) : undefined,
    idempotencyKey: String(row.idempotency_key),
    createdAt: new Date(String(row.created_at)).toISOString(),
    updatedAt: new Date(String(row.updated_at)).toISOString()
  };
}

function mapOutbox(row: Record<string, unknown>): OutboxMessageRecord {
  return {
    id: String(row.id),
    correlationId: String(row.correlation_id),
    channel: String(row.channel),
    chatId: String(row.chat_id),
    payload: row.payload ?? {},
    status: String(row.status) as "queued" | "sent" | "failed",
    retryCount: Number(row.retry_count ?? 0),
    nextRetryAt: row.next_retry_at ? new Date(String(row.next_retry_at)).toISOString() : null,
    lastError: row.last_error ? String(row.last_error) : null,
    createdAt: new Date(String(row.created_at)).toISOString(),
    updatedAt: new Date(String(row.updated_at)).toISOString()
  };
}

async function oneOrNull<T extends Record<string, unknown>>(result: QueryResult<T>): Promise<T | null> {
  if (result.rows.length === 0) {
    return null;
  }
  return result.rows[0];
}

export class PostgresRepository implements Repository {
  constructor(private readonly pool: Pool) {}

  async getInboundByUniqueKey(channel: string, chatId: string, messageId: string): Promise<{ id: string } | null> {
    const result = await this.pool.query<{ id: string }>(
      `SELECT id FROM inbound_messages WHERE channel = $1 AND chat_id = $2 AND message_id = $3 LIMIT 1`,
      [channel, chatId, messageId]
    );
    return oneOrNull(result);
  }

  async createInboundMessage(input: InboundMessageInput): Promise<void> {
    await this.pool.query(
      `INSERT INTO inbound_messages (id, channel, chat_id, message_id, text, raw_payload, correlation_id)
       VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
       ON CONFLICT (channel, chat_id, message_id) DO NOTHING`,
      [uuidv4(), input.channel, input.chatId, input.messageId, input.text ?? null, JSON.stringify(input.rawPayload), input.correlationId]
    );
  }

  async getActiveSession(chatId: string, nowIso: string): Promise<Session | null> {
    const result = await this.pool.query(
      `SELECT * FROM sessions
       WHERE chat_id = $1 AND status = 'active' AND expires_at > $2
       ORDER BY updated_at DESC
       LIMIT 1`,
      [chatId, nowIso]
    );
    const row = await oneOrNull(result);
    return row ? mapSession(row) : null;
  }

  async getSessionById(sessionId: string): Promise<Session | null> {
    const result = await this.pool.query(`SELECT * FROM sessions WHERE id = $1 LIMIT 1`, [sessionId]);
    const row = await oneOrNull(result);
    return row ? mapSession(row) : null;
  }

  async getSessionByCorrelationId(correlationId: string): Promise<Session | null> {
    const result = await this.pool.query(
      `SELECT * FROM sessions
       WHERE data->>'lastCorrelationId' = $1
       ORDER BY updated_at DESC
       LIMIT 1`,
      [correlationId]
    );
    const row = await oneOrNull(result);
    return row ? mapSession(row) : null;
  }

  async createSession(chatId: string, state: SessionState, expiresAtIso: string): Promise<Session> {
    const id = uuidv4();
    const result = await this.pool.query(
      `INSERT INTO sessions (id, chat_id, state, data, status, expires_at)
       VALUES ($1, $2, $3, $4::jsonb, 'active', $5)
       RETURNING *`,
      [id, chatId, state, JSON.stringify({}), expiresAtIso]
    );
    return mapSession(result.rows[0]);
  }

  async updateSession(sessionId: string, state: SessionState, data: SessionData, expiresAtIso: string): Promise<Session> {
    const result = await this.pool.query(
      `UPDATE sessions
       SET state = $2, data = $3::jsonb, expires_at = $4, updated_at = NOW()
       WHERE id = $1
       RETURNING *`,
      [sessionId, state, JSON.stringify(data), expiresAtIso]
    );
    return mapSession(result.rows[0]);
  }

  async closeSession(sessionId: string): Promise<void> {
    await this.pool.query(`UPDATE sessions SET status = 'closed', updated_at = NOW() WHERE id = $1`, [sessionId]);
  }

  async upsertPatient(chatId: string, fullName: string, mobile: string): Promise<Patient> {
    const result = await this.pool.query(
      `INSERT INTO patients (id, telegram_chat_id, full_name, mobile)
       VALUES ($1, $2, $3, $4)
       ON CONFLICT (telegram_chat_id)
       DO UPDATE SET full_name = EXCLUDED.full_name, mobile = EXCLUDED.mobile, updated_at = NOW()
       RETURNING *`,
      [uuidv4(), chatId, fullName, mobile]
    );
    return mapPatient(result.rows[0]);
  }

  async getAppointmentByIdempotencyKey(idempotencyKey: string): Promise<Appointment | null> {
    const result = await this.pool.query(`SELECT * FROM appointments WHERE idempotency_key = $1 LIMIT 1`, [idempotencyKey]);
    const row = await oneOrNull(result);
    return row ? mapAppointment(row) : null;
  }

  async getAppointmentById(appointmentId: string): Promise<Appointment | null> {
    const result = await this.pool.query(`SELECT * FROM appointments WHERE id = $1 LIMIT 1`, [appointmentId]);
    const row = await oneOrNull(result);
    return row ? mapAppointment(row) : null;
  }

  async getAppointmentByChatAndStart(chatId: string, startAtIso: string): Promise<Appointment | null> {
    const result = await this.pool.query(
      `SELECT * FROM appointments WHERE chat_id = $1 AND start_at = $2 ORDER BY created_at DESC LIMIT 1`,
      [chatId, startAtIso]
    );
    const row = await oneOrNull(result);
    return row ? mapAppointment(row) : null;
  }

  async createAppointment(input: CreateAppointmentInput): Promise<Appointment> {
    const existing = await this.getAppointmentByIdempotencyKey(input.idempotencyKey);
    if (existing) {
      return existing;
    }

    const result = await this.pool.query(
      `INSERT INTO appointments (
          id, patient_id, chat_id, doctor_id, start_at, end_at, status, calendar_event_id, idempotency_key, metadata
       ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
       RETURNING *`,
      [
        uuidv4(),
        input.patientId,
        input.chatId,
        input.doctorId,
        input.startAt,
        input.endAt,
        input.status,
        input.calendarEventId ?? null,
        input.idempotencyKey,
        JSON.stringify(input.metadata ?? {})
      ]
    );

    return mapAppointment(result.rows[0]);
  }

  async updateAppointmentStatus(appointmentId: string, status: Appointment["status"], calendarEventId?: string): Promise<void> {
    await this.pool.query(
      `UPDATE appointments
       SET status = $2, calendar_event_id = COALESCE($3, calendar_event_id), updated_at = NOW()
       WHERE id = $1`,
      [appointmentId, status, calendarEventId ?? null]
    );
  }

  async createToolRun(input: ToolRunInput): Promise<void> {
    await this.pool.query(
      `INSERT INTO tool_runs (
          id, correlation_id, session_id, tool_name, status, request_payload, response_payload, error
       ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8)`,
      [
        uuidv4(),
        input.correlationId,
        input.sessionId,
        input.toolName,
        input.status,
        JSON.stringify(input.requestPayload ?? {}),
        JSON.stringify(input.responsePayload ?? {}),
        input.error ?? null
      ]
    );
  }

  async createAuditEvent(input: AuditEventInput): Promise<void> {
    await this.pool.query(
      `INSERT INTO audit_events (id, correlation_id, event_type, entity_type, entity_id, payload)
       VALUES ($1, $2, $3, $4, $5, $6::jsonb)`,
      [uuidv4(), input.correlationId, input.eventType, input.entityType, input.entityId, JSON.stringify(input.payload ?? {})]
    );
  }

  async enqueueOutboxMessage(input: OutboxMessageInput): Promise<void> {
    await this.pool.query(
      `INSERT INTO outbox_messages (
          id, correlation_id, channel, chat_id, payload, status, retry_count, next_retry_at, last_error
       ) VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)`,
      [
        uuidv4(),
        input.correlationId,
        input.channel,
        input.chatId,
        JSON.stringify(input.payload ?? {}),
        input.status,
        input.retryCount ?? 0,
        input.nextRetryAt ?? null,
        input.lastError ?? null
      ]
    );
  }

  async getDueOutboxMessages(nowIso: string, limit: number): Promise<OutboxMessageRecord[]> {
    const result = await this.pool.query(
      `SELECT * FROM outbox_messages
       WHERE status = 'queued' AND (next_retry_at IS NULL OR next_retry_at <= $1)
       ORDER BY created_at ASC
       LIMIT $2`,
      [nowIso, limit]
    );
    return result.rows.map((row: Record<string, unknown>) => mapOutbox(row));
  }

  async getOutboxMessagesByStatus(status: "queued" | "sent" | "failed", limit: number): Promise<OutboxMessageRecord[]> {
    const result = await this.pool.query(
      `SELECT * FROM outbox_messages
       WHERE status = $1
       ORDER BY updated_at DESC
       LIMIT $2`,
      [status, limit]
    );
    return result.rows.map((row: Record<string, unknown>) => mapOutbox(row));
  }

  async updateOutboxMessageStatus(
    messageId: string,
    status: "queued" | "sent" | "failed",
    retryCount: number,
    nextRetryAt?: string | null,
    lastError?: string | null
  ): Promise<void> {
    await this.pool.query(
      `UPDATE outbox_messages
       SET status = $2, retry_count = $3, next_retry_at = $4, last_error = $5, updated_at = NOW()
       WHERE id = $1`,
      [messageId, status, retryCount, nextRetryAt ?? null, lastError ?? null]
    );
  }
}
