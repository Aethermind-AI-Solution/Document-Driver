import crypto from "node:crypto";
import dayjs from "dayjs";
import { config } from "../lib/config.js";
import {
  addMinutesUtc,
  isPastOrSameDay,
  IST,
  normalizePhone,
  nowInTimezone,
  nowUtcIso,
  parseUserDate,
  toDisplayFromUtc,
  toUtcFromTz
} from "../lib/time.js";
import { detectIntent, detectPeriod, extractName, isConfirmation, isNegative } from "../domain/intents.js";
import { Replies } from "../domain/replies.js";
import { generateSlotCandidates, parseSlotText, selectFreeSlots } from "../domain/schedule.js";
import { NextAction, Session, SessionAdvanceResponse, SessionData, ToolResultResponse, ToolName } from "../domain/types.js";
import { Repository } from "../repositories/repository.js";

export interface AdvanceSessionInput {
  chatId: string;
  messageId: string;
  userText: string;
  correlationId: string;
}

export interface InboundInput {
  channel: "telegram";
  chatId: string;
  messageId: string;
  text?: string;
  rawPayload: unknown;
  correlationId: string;
}

export interface ToolResultInput {
  correlationId: string;
  toolName: ToolName;
  toolOutput: Record<string, unknown>;
}

export interface ConfirmAppointmentInput {
  sessionId: string;
  idempotencyKey: string;
}

export interface CancelAppointmentInput {
  appointmentId?: string;
  chatId?: string;
  date?: string;
  time?: string;
}

export interface RescheduleAppointmentInput {
  appointmentId?: string;
  chatId?: string;
  date?: string;
  time?: string;
}

export interface ReminderMessageInput {
  patientName: string;
  appointmentTime: string;
  chatId: string;
  language?: "en" | "hi";
}

function computeExpiryIso(): string {
  return dayjs().add(config.SESSION_TTL_HOURS, "hour").toISOString();
}

function buildToolResponse(session: Session, tool: ToolName, payload: Record<string, unknown>, correlationId: string): SessionAdvanceResponse {
  return {
    next_action: { kind: "RUN_TOOL", tool, payload },
    state: session.state,
    tool_request: {
      correlation_id: correlationId,
      tool,
      payload
    }
  };
}

function buildAskResponse(session: Session, replyText: string): SessionAdvanceResponse {
  return {
    next_action: { kind: "ASK_USER", reply_text: replyText },
    state: session.state,
    reply_text: replyText
  };
}

function makeIdempotencyKey(chatId: string, selectedSlotUtc: string): string {
  const value = `${chatId}:${selectedSlotUtc}`;
  return crypto.createHash("sha256").update(value).digest("hex");
}

function extractBusySlots(toolOutput: Record<string, unknown>): string[] {
  const busySlots = toolOutput.busy_slots_utc;
  if (Array.isArray(busySlots)) {
    return busySlots.filter((value): value is string => typeof value === "string");
  }

  const events = toolOutput.events;
  if (!Array.isArray(events)) {
    return [];
  }

  const busy: string[] = [];
  for (const event of events) {
    if (typeof event !== "object" || event === null) {
      continue;
    }
    const maybeStart = (event as Record<string, unknown>).start as Record<string, unknown> | undefined;
    const value = maybeStart?.dateTime;
    if (typeof value === "string") {
      busy.push(new Date(value).toISOString());
    }
  }
  return busy;
}

export class BookingCoreService {
  constructor(private readonly repo: Repository) {}

  async ingestInbound(input: InboundInput): Promise<{ accepted: boolean; duplicate: boolean }> {
    const existing = await this.repo.getInboundByUniqueKey(input.channel, input.chatId, input.messageId);
    if (existing) {
      return { accepted: false, duplicate: true };
    }

    await this.repo.createInboundMessage({
      channel: input.channel,
      chatId: input.chatId,
      messageId: input.messageId,
      text: input.text,
      rawPayload: input.rawPayload,
      correlationId: input.correlationId
    });

    await this.repo.createAuditEvent({
      correlationId: input.correlationId,
      eventType: "INBOUND_ACCEPTED",
      entityType: "inbound_message",
      entityId: `${input.channel}:${input.chatId}:${input.messageId}`,
      payload: { hasText: Boolean(input.text) }
    });

    return { accepted: true, duplicate: false };
  }

  async advanceSession(input: AdvanceSessionInput): Promise<SessionAdvanceResponse> {
    const nowIso = nowUtcIso();
    let session = await this.repo.getActiveSession(input.chatId, nowIso);

    if (!session) {
      session = await this.repo.createSession(input.chatId, "NEED_NAME", computeExpiryIso());
    }

    const userText = input.userText.trim();
    const language = session.data.preferredLanguage ?? (/[\u0900-\u097F]/.test(userText) ? "hi" : "en");

    const data: SessionData = {
      ...session.data,
      preferredLanguage: language
    };

    if (!userText) {
      const reply = Replies.nonTextFallback(language);
      session = await this.repo.updateSession(session.id, session.state, data, computeExpiryIso());
      return buildAskResponse(session, reply);
    }

    if (session.state === "BOOKED") {
      const intent = detectIntent(userText);
      if (intent === "CANCEL" && data.calendarEventId) {
        data.lastCorrelationId = input.correlationId;
        session = await this.repo.updateSession(session.id, "BOOKED", data, computeExpiryIso());
        return buildToolResponse(
          session,
          "CALENDAR_DELETE",
          {
            event_id: data.calendarEventId,
            appointment_id: data.appointmentId,
            chat_id: session.chatId,
            reason: "user_requested_cancel"
          },
          input.correlationId
        );
      }

      if (intent === "RESCHEDULE" && data.calendarEventId) {
        data.lastCorrelationId = input.correlationId;
        session = await this.repo.updateSession(session.id, "RESCHEDULE_PENDING", data, computeExpiryIso());
        return buildToolResponse(
          session,
          "CALENDAR_DELETE",
          {
            event_id: data.calendarEventId,
            appointment_id: data.appointmentId,
            chat_id: session.chatId,
            reason: "user_requested_reschedule"
          },
          input.correlationId
        );
      }

      const done = Replies.confirmationComplete(
        language,
        data.patientName ?? "",
        data.mobile ?? "",
        data.requestedDate ?? "",
        data.selectedSlotDisplay ?? ""
      );
      return {
        next_action: { kind: "DONE", reply_text: done },
        state: session.state,
        reply_text: done
      };
    }

    switch (session.state) {
      case "NEED_NAME": {
        const name = extractName(userText);
        if (!name) {
          const reply = Replies.askName(language);
          session = await this.repo.updateSession(session.id, session.state, data, computeExpiryIso());
          return buildAskResponse(session, reply);
        }

        data.patientName = name;
        session = await this.repo.updateSession(session.id, "NEED_MOBILE", data, computeExpiryIso());
        return buildAskResponse(session, Replies.askMobile(language, name));
      }

      case "NEED_MOBILE": {
        const mobile = normalizePhone(userText);
        if (!mobile) {
          const reply =
            language === "hi"
              ? "Please valid 10-digit mobile number bhejiye."
              : "Please share a valid 10-digit mobile number.";
          session = await this.repo.updateSession(session.id, session.state, data, computeExpiryIso());
          return buildAskResponse(session, reply);
        }

        data.mobile = mobile;
        session = await this.repo.updateSession(session.id, "NEED_DATE", data, computeExpiryIso());
        return buildAskResponse(session, Replies.askDate(language));
      }

      case "NEED_DATE": {
        const parsedDate = parseUserDate(userText, config.TIMEZONE || IST);
        if (!parsedDate) {
          const reply =
            language === "hi"
              ? "Valid date bhejiye, format DD-MM-YYYY."
              : "Please share a valid date in DD-MM-YYYY format.";
          session = await this.repo.updateSession(session.id, session.state, data, computeExpiryIso());
          return buildAskResponse(session, reply);
        }

        if (isPastOrSameDay(parsedDate, config.TIMEZONE || IST)) {
          session = await this.repo.updateSession(session.id, session.state, data, computeExpiryIso());
          return buildAskResponse(session, Replies.rejectPastOrSameDay(language));
        }

        data.requestedDate = parsedDate.format("YYYY-MM-DD");
        data.period = undefined;
        data.availableSlots = undefined;
        data.selectedSlotUtc = undefined;
        data.selectedSlotDisplay = undefined;
        data.selectedSlotEndUtc = undefined;

        session = await this.repo.updateSession(session.id, "NEED_PERIOD", data, computeExpiryIso());
        return buildAskResponse(session, Replies.askPeriod(language));
      }

      case "NEED_PERIOD": {
        const period = detectPeriod(userText);
        if (!period || !data.requestedDate) {
          session = await this.repo.updateSession(session.id, session.state, data, computeExpiryIso());
          return buildAskResponse(session, Replies.askPeriod(language));
        }

        data.period = period;
        const candidates = generateSlotCandidates(data.requestedDate, period, 10);
        if (candidates.length === 0) {
          session = await this.repo.updateSession(session.id, session.state, data, computeExpiryIso());
          return buildAskResponse(session, Replies.noSlotsForPeriod(language));
        }

        data.availableSlots = candidates;
        data.lastCorrelationId = input.correlationId;
        session = await this.repo.updateSession(session.id, "NEED_SLOT_SELECTION", data, computeExpiryIso());

        const payload = {
          chat_id: session.chatId,
          requested_date: data.requestedDate,
          period,
          slot_minutes: 10,
          time_min_utc: candidates[0].startUtc,
          time_max_utc: candidates[candidates.length - 1].endUtc,
          candidate_slots_utc: candidates.map((slot) => slot.startUtc)
        };

        return buildToolResponse(session, "CALENDAR_CHECK", payload, input.correlationId);
      }

      case "NEED_SLOT_SELECTION": {
        if (!data.availableSlots || data.availableSlots.length === 0) {
          session = await this.repo.updateSession(session.id, "NEED_PERIOD", data, computeExpiryIso());
          return buildAskResponse(session, Replies.askPeriod(language));
        }

        const normalized = parseSlotText(userText);
        if (!normalized) {
          session = await this.repo.updateSession(session.id, session.state, data, computeExpiryIso());
          return buildAskResponse(session, Replies.invalidSlot(language));
        }

        const selected = data.availableSlots.find((slot) => slot.display.toUpperCase() === normalized.toUpperCase());
        if (!selected) {
          session = await this.repo.updateSession(session.id, session.state, data, computeExpiryIso());
          return buildAskResponse(session, Replies.chooseSlot(language, data.availableSlots.map((slot) => slot.display)));
        }

        data.selectedSlotUtc = selected.startUtc;
        data.selectedSlotEndUtc = selected.endUtc;
        data.selectedSlotDisplay = selected.display;

        const summary =
          language === "hi"
            ? `Name: ${data.patientName}\nMobile: ${data.mobile}\nDate: ${data.requestedDate}\nTime: ${selected.display}`
            : `Name: ${data.patientName}\nMobile: ${data.mobile}\nDate: ${data.requestedDate}\nTime: ${selected.display}`;

        session = await this.repo.updateSession(session.id, "NEED_CONFIRMATION", data, computeExpiryIso());
        return buildAskResponse(session, Replies.askConfirmation(language, summary));
      }

      case "NEED_CONFIRMATION": {
        if (isNegative(userText)) {
          data.requestedDate = undefined;
          data.period = undefined;
          data.availableSlots = undefined;
          data.selectedSlotUtc = undefined;
          data.selectedSlotEndUtc = undefined;
          data.selectedSlotDisplay = undefined;
          session = await this.repo.updateSession(session.id, "NEED_DATE", data, computeExpiryIso());
          return buildAskResponse(session, `${Replies.confirmationDeclined(language)}\n${Replies.askDate(language)}`);
        }

        if (!isConfirmation(userText) || !data.selectedSlotUtc || !data.selectedSlotEndUtc) {
          session = await this.repo.updateSession(session.id, session.state, data, computeExpiryIso());
          const reply =
            language === "hi"
              ? "Confirm karne ke liye 'yes' likhiye, ya slot badalne ke liye 'no' likhiye."
              : "Please type 'yes' to confirm or 'no' to choose another slot.";
          return buildAskResponse(session, reply);
        }

        const idempotencyKey = makeIdempotencyKey(input.chatId, data.selectedSlotUtc);
        data.pendingIdempotencyKey = idempotencyKey;
        data.lastCorrelationId = input.correlationId;

        session = await this.repo.updateSession(session.id, "NEED_CONFIRMATION", data, computeExpiryIso());

        return buildToolResponse(
          session,
          "CALENDAR_CREATE",
          {
            start_utc: data.selectedSlotUtc,
            end_utc: data.selectedSlotEndUtc,
            summary: `Booking: ${data.patientName} - ${data.mobile}`,
            idempotency_key: idempotencyKey,
            chat_id: session.chatId
          },
          input.correlationId
        );
      }

      case "RESCHEDULE_PENDING": {
        session = await this.repo.updateSession(session.id, "NEED_DATE", data, computeExpiryIso());
        return buildAskResponse(session, Replies.askDate(language));
      }

      case "CANCELLED": {
        session = await this.repo.updateSession(session.id, "NEED_DATE", data, computeExpiryIso());
        return buildAskResponse(session, Replies.askDate(language));
      }

      default: {
        const reply = Replies.askName(language);
        session = await this.repo.updateSession(session.id, "NEED_NAME", data, computeExpiryIso());
        return buildAskResponse(session, reply);
      }
    }
  }

  async handleToolResult(input: ToolResultInput): Promise<ToolResultResponse> {
    const session = await this.repo.getSessionByCorrelationId(input.correlationId);
    if (!session) {
      return {
        next_action: { kind: "ASK_USER", reply_text: "Session not found for tool response." },
        state: "NEED_NAME",
        reply_text: "Session not found for tool response.",
        terminal: false
      };
    }

    const language = session.data.preferredLanguage ?? "en";

    await this.repo.createToolRun({
      correlationId: input.correlationId,
      sessionId: session.id,
      toolName: input.toolName,
      status: input.toolOutput.success === false ? "failed" : "success",
      requestPayload: {},
      responsePayload: input.toolOutput,
      error: typeof input.toolOutput.error === "string" ? input.toolOutput.error : undefined
    });

    if (input.toolOutput.success === false) {
      await this.repo.enqueueOutboxMessage({
        correlationId: input.correlationId,
        channel: "telegram",
        chatId: session.chatId,
        payload: { reason: "tool_failure", tool: input.toolName },
        status: "queued",
        retryCount: 0,
        nextRetryAt: dayjs().add(2, "minute").toISOString(),
        lastError: typeof input.toolOutput.error === "string" ? input.toolOutput.error : null
      });

      const fallback = Replies.toolFailureFallback(language);
      return {
        next_action: { kind: "ASK_USER", reply_text: fallback },
        state: session.state,
        reply_text: fallback,
        terminal: false
      };
    }

    if (input.toolName === "CALENDAR_CHECK") {
      if (!session.data.requestedDate || !session.data.period || !session.data.availableSlots) {
        const reply = Replies.askDate(language);
        return {
          next_action: { kind: "ASK_USER", reply_text: reply },
          state: "NEED_DATE",
          reply_text: reply,
          terminal: false
        };
      }

      const busySlots = extractBusySlots(input.toolOutput);
      const available = selectFreeSlots(session.data.availableSlots, busySlots, 3);

      if (available.length === 0) {
        const updated = await this.repo.updateSession(
          session.id,
          "NEED_PERIOD",
          {
            ...session.data,
            availableSlots: undefined
          },
          computeExpiryIso()
        );

        const reply = Replies.noSlotsForPeriod(language);
        return {
          next_action: { kind: "ASK_USER", reply_text: reply },
          state: updated.state,
          reply_text: reply,
          terminal: false
        };
      }

      const updated = await this.repo.updateSession(
        session.id,
        "NEED_SLOT_SELECTION",
        {
          ...session.data,
          availableSlots: available
        },
        computeExpiryIso()
      );

      const reply = Replies.chooseSlot(language, available.map((slot) => slot.display));
      return {
        next_action: { kind: "ASK_USER", reply_text: reply },
        state: updated.state,
        reply_text: reply,
        terminal: false
      };
    }

    if (input.toolName === "CALENDAR_CREATE") {
      const eventId = input.toolOutput.event_id;
      if (typeof eventId !== "string") {
        const fallback = Replies.toolFailureFallback(language);
        return {
          next_action: { kind: "ASK_USER", reply_text: fallback },
          state: session.state,
          reply_text: fallback,
          terminal: false
        };
      }

      const patient = await this.repo.upsertPatient(
        session.chatId,
        session.data.patientName ?? "Unknown",
        session.data.mobile ?? "0000000000"
      );

      const appointment = await this.repo.createAppointment({
        patientId: patient.id,
        chatId: session.chatId,
        doctorId: config.DOCTOR_ID,
        startAt: session.data.selectedSlotUtc ?? nowUtcIso(),
        endAt: session.data.selectedSlotEndUtc ?? addMinutesUtc(session.data.selectedSlotUtc ?? nowUtcIso(), 10),
        status: "confirmed",
        calendarEventId: eventId,
        idempotencyKey: session.data.pendingIdempotencyKey ?? crypto.randomUUID(),
        metadata: {
          requestedDate: session.data.requestedDate,
          time: session.data.selectedSlotDisplay
        }
      });

      await this.repo.updateSession(
        session.id,
        "BOOKED",
        {
          ...session.data,
          appointmentId: appointment.id,
          calendarEventId: eventId
        },
        computeExpiryIso()
      );

      const payload = {
        appointment_id: appointment.id,
        patient_name: session.data.patientName,
        mobile: session.data.mobile,
        date: session.data.requestedDate,
        time: session.data.selectedSlotDisplay,
        status: "Confirmed",
        telegram_chat_id: session.chatId,
        calendar_event_id: eventId
      };

      return {
        next_action: { kind: "RUN_TOOL", tool: "SHEET_UPSERT", payload },
        state: "BOOKED",
        terminal: false,
        tool_request: {
          correlation_id: input.correlationId,
          tool: "SHEET_UPSERT",
          payload
        }
      };
    }

    if (input.toolName === "SHEET_UPSERT") {
      const reply = Replies.confirmationComplete(
        language,
        session.data.patientName ?? "",
        session.data.mobile ?? "",
        session.data.requestedDate ?? "",
        session.data.selectedSlotDisplay ?? ""
      );

      return {
        next_action: { kind: "DONE", reply_text: reply },
        state: "BOOKED",
        reply_text: reply,
        terminal: true
      };
    }

    if (input.toolName === "CALENDAR_DELETE") {
      if (session.data.appointmentId) {
        await this.repo.updateAppointmentStatus(session.data.appointmentId, "cancelled");
      }

      const nextState = session.state === "RESCHEDULE_PENDING" ? "NEED_DATE" : "CANCELLED";
      const updated = await this.repo.updateSession(
        session.id,
        nextState,
        {
          ...session.data,
          calendarEventId: undefined,
          appointmentId: undefined,
          selectedSlotUtc: undefined,
          selectedSlotEndUtc: undefined,
          selectedSlotDisplay: undefined,
          availableSlots: undefined,
          period: undefined,
          requestedDate: undefined
        },
        computeExpiryIso()
      );

      const reply = nextState === "NEED_DATE" ? Replies.askDate(language) : Replies.cancelAck(language);

      return {
        next_action: { kind: "DONE", reply_text: reply },
        state: updated.state,
        reply_text: reply,
        terminal: true
      };
    }

    return {
      next_action: { kind: "ASK_USER", reply_text: Replies.toolFailureFallback(language) },
      state: session.state,
      reply_text: Replies.toolFailureFallback(language),
      terminal: false
    };
  }

  async confirmAppointment(input: ConfirmAppointmentInput) {
    const session = await this.repo.getSessionById(input.sessionId);
    if (!session) {
      throw new Error("Session not found");
    }

    if (!session.data.selectedSlotUtc || !session.data.selectedSlotEndUtc || !session.data.patientName || !session.data.mobile) {
      throw new Error("Session does not contain complete booking details");
    }

    const existing = await this.repo.getAppointmentByIdempotencyKey(input.idempotencyKey);
    if (existing) {
      return {
        appointment_id: existing.id,
        calendar_event_id: existing.calendarEventId ?? null,
        status: existing.status
      };
    }

    const patient = await this.repo.upsertPatient(session.chatId, session.data.patientName, session.data.mobile);
    const appointment = await this.repo.createAppointment({
      patientId: patient.id,
      chatId: session.chatId,
      doctorId: config.DOCTOR_ID,
      startAt: session.data.selectedSlotUtc,
      endAt: session.data.selectedSlotEndUtc,
      status: "held",
      idempotencyKey: input.idempotencyKey,
      metadata: {
        source: "confirm_endpoint"
      }
    });

    return {
      appointment_id: appointment.id,
      calendar_event_id: appointment.calendarEventId ?? null,
      status: appointment.status
    };
  }

  async cancelAppointment(input: CancelAppointmentInput) {
    let appointment = input.appointmentId ? await this.repo.getAppointmentById(input.appointmentId) : null;

    if (!appointment && input.chatId && input.date && input.time) {
      const startUtc = this.buildUtcFromDateAndTime(input.date, input.time);
      appointment = await this.repo.getAppointmentByChatAndStart(input.chatId, startUtc);
    }

    if (!appointment) {
      throw new Error("Appointment not found");
    }

    await this.repo.updateAppointmentStatus(appointment.id, "cancelled");

    return {
      appointment_id: appointment.id,
      status: "cancelled"
    };
  }

  async rescheduleAppointment(input: RescheduleAppointmentInput) {
    let appointment = input.appointmentId ? await this.repo.getAppointmentById(input.appointmentId) : null;

    if (!appointment && input.chatId && input.date && input.time) {
      const startUtc = this.buildUtcFromDateAndTime(input.date, input.time);
      appointment = await this.repo.getAppointmentByChatAndStart(input.chatId, startUtc);
    }

    if (!appointment) {
      throw new Error("Appointment not found");
    }

    await this.repo.updateAppointmentStatus(appointment.id, "rescheduled");

    let session = await this.repo.getActiveSession(appointment.chatId, nowUtcIso());
    if (!session) {
      session = await this.repo.createSession(appointment.chatId, "NEED_DATE", computeExpiryIso());
    } else {
      await this.repo.updateSession(
        session.id,
        "NEED_DATE",
        {
          ...session.data,
          rescheduleTargetAppointmentId: appointment.id,
          requestedDate: undefined,
          period: undefined,
          availableSlots: undefined,
          selectedSlotUtc: undefined,
          selectedSlotEndUtc: undefined,
          selectedSlotDisplay: undefined
        },
        computeExpiryIso()
      );
    }

    return {
      appointment_id: appointment.id,
      status: "rescheduled",
      next_state: "NEED_DATE"
    };
  }

  async getDueOutbox(limit = 50) {
    return this.repo.getDueOutboxMessages(nowUtcIso(), limit);
  }

  async getFailedOutbox(limit = 50) {
    return this.repo.getOutboxMessagesByStatus("failed", limit);
  }

  async markOutboxMessage(
    messageId: string,
    status: "queued" | "sent" | "failed",
    retryCount: number,
    nextRetryAt?: string | null,
    lastError?: string | null
  ) {
    await this.repo.updateOutboxMessageStatus(messageId, status, retryCount, nextRetryAt, lastError);
    return { ok: true };
  }

  buildReminderMessage(input: ReminderMessageInput) {
    const firstName = input.patientName.trim().split(/\s+/)[0] ?? "Patient";
    const lang = input.language ?? "en";
    const text =
      lang === "hi"
        ? `Namaste ${firstName}, aapka homeopathy consultation aaj ${input.appointmentTime} par hai. Kripya 5 minute pehle clinic pahunch jaiye.`
        : `Hi ${firstName}, this is a reminder that your homeopathy consultation is today in 1 hour at ${input.appointmentTime}. Please arrive 5 minutes early.`;
    return {
      chat_id: input.chatId,
      text
    };
  }

  private buildUtcFromDateAndTime(date: string, time: string): string {
    return toUtcFromTz(`${date} ${time}`, config.TIMEZONE);
  }

  formatSlotForDisplay(slotUtc: string): string {
    return toDisplayFromUtc(slotUtc, config.TIMEZONE);
  }

  getNowInTimezone() {
    return nowInTimezone(config.TIMEZONE).toISOString();
  }
}
