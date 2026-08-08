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

interface InboundRecord {
  id: string;
  channel: string;
  chatId: string;
  messageId: string;
}

export class InMemoryRepository implements Repository {
  private inbound = new Map<string, InboundRecord>();
  private sessions = new Map<string, Session>();
  private patients = new Map<string, Patient>();
  private appointments = new Map<string, Appointment>();
  private appointmentByIdempotency = new Map<string, string>();
  private toolRuns: ToolRunInput[] = [];
  private audits: AuditEventInput[] = [];
  private outbox = new Map<string, OutboxMessageRecord>();

  async getInboundByUniqueKey(channel: string, chatId: string, messageId: string): Promise<{ id: string } | null> {
    const key = `${channel}:${chatId}:${messageId}`;
    const found = this.inbound.get(key);
    return found ? { id: found.id } : null;
  }

  async createInboundMessage(input: InboundMessageInput): Promise<void> {
    const key = `${input.channel}:${input.chatId}:${input.messageId}`;
    if (!this.inbound.has(key)) {
      this.inbound.set(key, {
        id: uuidv4(),
        channel: input.channel,
        chatId: input.chatId,
        messageId: input.messageId
      });
    }
  }

  async getActiveSession(chatId: string, nowIso: string): Promise<Session | null> {
    for (const session of this.sessions.values()) {
      if (session.chatId !== chatId) {
        continue;
      }
      if (session.status !== "active") {
        continue;
      }
      if (new Date(session.expiresAt).getTime() <= new Date(nowIso).getTime()) {
        continue;
      }
      return session;
    }
    return null;
  }

  async getSessionById(sessionId: string): Promise<Session | null> {
    return this.sessions.get(sessionId) ?? null;
  }

  async getSessionByCorrelationId(correlationId: string): Promise<Session | null> {
    for (const session of this.sessions.values()) {
      if (session.data.lastCorrelationId === correlationId) {
        return session;
      }
    }
    return null;
  }

  async createSession(chatId: string, state: SessionState, expiresAtIso: string): Promise<Session> {
    const now = new Date().toISOString();
    const session: Session = {
      id: uuidv4(),
      chatId,
      state,
      data: {},
      status: "active",
      createdAt: now,
      updatedAt: now,
      expiresAt: expiresAtIso
    };
    this.sessions.set(session.id, session);
    return session;
  }

  async updateSession(sessionId: string, state: SessionState, data: SessionData, expiresAtIso: string): Promise<Session> {
    const session = this.sessions.get(sessionId);
    if (!session) {
      throw new Error(`Session ${sessionId} not found`);
    }
    const updated: Session = {
      ...session,
      state,
      data,
      expiresAt: expiresAtIso,
      updatedAt: new Date().toISOString()
    };
    this.sessions.set(sessionId, updated);
    return updated;
  }

  async closeSession(sessionId: string): Promise<void> {
    const session = this.sessions.get(sessionId);
    if (!session) {
      return;
    }
    this.sessions.set(sessionId, {
      ...session,
      status: "closed",
      updatedAt: new Date().toISOString()
    });
  }

  async upsertPatient(chatId: string, fullName: string, mobile: string): Promise<Patient> {
    for (const patient of this.patients.values()) {
      if (patient.chatId === chatId) {
        const updated = {
          ...patient,
          fullName,
          mobile,
          updatedAt: new Date().toISOString()
        };
        this.patients.set(updated.id, updated);
        return updated;
      }
    }

    const now = new Date().toISOString();
    const patient: Patient = {
      id: uuidv4(),
      chatId,
      fullName,
      mobile,
      createdAt: now,
      updatedAt: now
    };
    this.patients.set(patient.id, patient);
    return patient;
  }

  async getAppointmentByIdempotencyKey(idempotencyKey: string): Promise<Appointment | null> {
    const appointmentId = this.appointmentByIdempotency.get(idempotencyKey);
    if (!appointmentId) {
      return null;
    }
    return this.appointments.get(appointmentId) ?? null;
  }

  async getAppointmentById(appointmentId: string): Promise<Appointment | null> {
    return this.appointments.get(appointmentId) ?? null;
  }

  async getAppointmentByChatAndStart(chatId: string, startAtIso: string): Promise<Appointment | null> {
    for (const appointment of this.appointments.values()) {
      if (appointment.chatId === chatId && appointment.startAt === startAtIso) {
        return appointment;
      }
    }
    return null;
  }

  async createAppointment(input: CreateAppointmentInput): Promise<Appointment> {
    const existing = await this.getAppointmentByIdempotencyKey(input.idempotencyKey);
    if (existing) {
      return existing;
    }

    const now = new Date().toISOString();
    const appointment: Appointment = {
      id: uuidv4(),
      patientId: input.patientId,
      chatId: input.chatId,
      doctorId: input.doctorId,
      startAt: input.startAt,
      endAt: input.endAt,
      status: input.status,
      calendarEventId: input.calendarEventId,
      idempotencyKey: input.idempotencyKey,
      createdAt: now,
      updatedAt: now
    };

    this.appointments.set(appointment.id, appointment);
    this.appointmentByIdempotency.set(appointment.idempotencyKey, appointment.id);
    return appointment;
  }

  async updateAppointmentStatus(appointmentId: string, status: Appointment["status"], calendarEventId?: string): Promise<void> {
    const appointment = this.appointments.get(appointmentId);
    if (!appointment) {
      return;
    }

    this.appointments.set(appointmentId, {
      ...appointment,
      status,
      calendarEventId: calendarEventId ?? appointment.calendarEventId,
      updatedAt: new Date().toISOString()
    });
  }

  async createToolRun(input: ToolRunInput): Promise<void> {
    this.toolRuns.push(input);
  }

  async createAuditEvent(input: AuditEventInput): Promise<void> {
    this.audits.push(input);
  }

  async enqueueOutboxMessage(input: OutboxMessageInput): Promise<void> {
    const now = new Date().toISOString();
    const id = uuidv4();
    this.outbox.set(id, {
      id,
      correlationId: input.correlationId,
      channel: input.channel,
      chatId: input.chatId,
      payload: input.payload,
      status: input.status,
      retryCount: input.retryCount ?? 0,
      nextRetryAt: input.nextRetryAt ?? null,
      lastError: input.lastError ?? null,
      createdAt: now,
      updatedAt: now
    });
  }

  async getDueOutboxMessages(nowIso: string, limit: number): Promise<OutboxMessageRecord[]> {
    const nowTs = new Date(nowIso).getTime();
    const due = [...this.outbox.values()]
      .filter((item) => {
        if (item.status !== "queued") {
          return false;
        }
        if (!item.nextRetryAt) {
          return true;
        }
        return new Date(item.nextRetryAt).getTime() <= nowTs;
      })
      .sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime())
      .slice(0, limit);
    return due;
  }

  async getOutboxMessagesByStatus(status: "queued" | "sent" | "failed", limit: number): Promise<OutboxMessageRecord[]> {
    return [...this.outbox.values()]
      .filter((item) => item.status === status)
      .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
      .slice(0, limit);
  }

  async updateOutboxMessageStatus(
    messageId: string,
    status: "queued" | "sent" | "failed",
    retryCount: number,
    nextRetryAt?: string | null,
    lastError?: string | null
  ): Promise<void> {
    const existing = this.outbox.get(messageId);
    if (!existing) {
      return;
    }
    this.outbox.set(messageId, {
      ...existing,
      status,
      retryCount,
      nextRetryAt: nextRetryAt ?? null,
      lastError: lastError ?? null,
      updatedAt: new Date().toISOString()
    });
  }

  snapshot() {
    return {
      inbound: [...this.inbound.values()],
      sessions: [...this.sessions.values()],
      patients: [...this.patients.values()],
      appointments: [...this.appointments.values()],
      toolRuns: this.toolRuns,
      audits: this.audits,
      outbox: [...this.outbox.values()]
    };
  }
}
