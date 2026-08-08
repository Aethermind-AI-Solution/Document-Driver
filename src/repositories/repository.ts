import { Appointment, Patient, Session, SessionData, SessionState } from "../domain/types.js";

export interface InboundMessageInput {
  channel: string;
  chatId: string;
  messageId: string;
  text?: string;
  rawPayload: unknown;
  correlationId: string;
}

export interface ToolRunInput {
  correlationId: string;
  sessionId: string;
  toolName: string;
  status: "success" | "failed";
  requestPayload: unknown;
  responsePayload: unknown;
  error?: string;
}

export interface AuditEventInput {
  correlationId: string;
  eventType: string;
  entityType: string;
  entityId: string;
  payload: unknown;
}

export interface OutboxMessageInput {
  correlationId: string;
  channel: string;
  chatId: string;
  payload: unknown;
  status: "queued" | "sent" | "failed";
  retryCount?: number;
  nextRetryAt?: string | null;
  lastError?: string | null;
}

export interface OutboxMessageRecord {
  id: string;
  correlationId: string;
  channel: string;
  chatId: string;
  payload: unknown;
  status: "queued" | "sent" | "failed";
  retryCount: number;
  nextRetryAt?: string | null;
  lastError?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface CreateAppointmentInput {
  patientId: string;
  chatId: string;
  doctorId: string;
  startAt: string;
  endAt: string;
  status: Appointment["status"];
  calendarEventId?: string;
  idempotencyKey: string;
  metadata?: unknown;
}

export interface Repository {
  getInboundByUniqueKey(channel: string, chatId: string, messageId: string): Promise<{ id: string } | null>;
  createInboundMessage(input: InboundMessageInput): Promise<void>;

  getActiveSession(chatId: string, nowIso: string): Promise<Session | null>;
  getSessionById(sessionId: string): Promise<Session | null>;
  getSessionByCorrelationId(correlationId: string): Promise<Session | null>;
  createSession(chatId: string, state: SessionState, expiresAtIso: string): Promise<Session>;
  updateSession(sessionId: string, state: SessionState, data: SessionData, expiresAtIso: string): Promise<Session>;
  closeSession(sessionId: string): Promise<void>;

  upsertPatient(chatId: string, fullName: string, mobile: string): Promise<Patient>;

  getAppointmentByIdempotencyKey(idempotencyKey: string): Promise<Appointment | null>;
  getAppointmentById(appointmentId: string): Promise<Appointment | null>;
  getAppointmentByChatAndStart(chatId: string, startAtIso: string): Promise<Appointment | null>;
  createAppointment(input: CreateAppointmentInput): Promise<Appointment>;
  updateAppointmentStatus(appointmentId: string, status: Appointment["status"], calendarEventId?: string): Promise<void>;

  createToolRun(input: ToolRunInput): Promise<void>;
  createAuditEvent(input: AuditEventInput): Promise<void>;
  enqueueOutboxMessage(input: OutboxMessageInput): Promise<void>;
  getDueOutboxMessages(nowIso: string, limit: number): Promise<OutboxMessageRecord[]>;
  getOutboxMessagesByStatus(status: "queued" | "sent" | "failed", limit: number): Promise<OutboxMessageRecord[]>;
  updateOutboxMessageStatus(
    messageId: string,
    status: "queued" | "sent" | "failed",
    retryCount: number,
    nextRetryAt?: string | null,
    lastError?: string | null
  ): Promise<void>;
}
