export type SessionState =
  | "NEED_NAME"
  | "NEED_MOBILE"
  | "NEED_DATE"
  | "NEED_PERIOD"
  | "NEED_SLOT_SELECTION"
  | "NEED_CONFIRMATION"
  | "BOOKED"
  | "CANCELLED"
  | "RESCHEDULE_PENDING";

export type Period = "MORNING" | "EVENING";

export type ToolName = "CALENDAR_CHECK" | "CALENDAR_CREATE" | "CALENDAR_DELETE" | "SHEET_UPSERT";

export type NextAction =
  | { kind: "ASK_USER"; reply_text: string }
  | { kind: "RUN_TOOL"; tool: ToolName; payload: Record<string, unknown> }
  | { kind: "DONE"; reply_text: string };

export interface SessionData {
  patientName?: string;
  mobile?: string;
  requestedDate?: string;
  period?: Period;
  availableSlots?: Array<{
    startUtc: string;
    endUtc: string;
    display: string;
  }>;
  selectedSlotUtc?: string;
  selectedSlotEndUtc?: string;
  selectedSlotDisplay?: string;
  calendarEventId?: string;
  appointmentId?: string;
  pendingIdempotencyKey?: string;
  lastCorrelationId?: string;
  preferredLanguage?: "en" | "hi";
  rescheduleTargetAppointmentId?: string;
}

export interface Session {
  id: string;
  chatId: string;
  state: SessionState;
  data: SessionData;
  expiresAt: string;
  createdAt: string;
  updatedAt: string;
  status: "active" | "closed";
}

export interface Patient {
  id: string;
  chatId: string;
  fullName: string;
  mobile: string;
  createdAt: string;
  updatedAt: string;
}

export interface Appointment {
  id: string;
  patientId: string;
  chatId: string;
  doctorId: string;
  startAt: string;
  endAt: string;
  status: "held" | "confirmed" | "cancelled" | "rescheduled";
  calendarEventId?: string;
  idempotencyKey: string;
  createdAt: string;
  updatedAt: string;
}

export interface SessionAdvanceResponse {
  next_action: NextAction;
  state: SessionState;
  reply_text?: string;
  tool_request?: {
    correlation_id: string;
    tool: ToolName;
    payload: Record<string, unknown>;
  };
}

export interface ToolResultResponse {
  next_action: NextAction;
  state: SessionState;
  reply_text?: string;
  terminal: boolean;
  tool_request?: {
    correlation_id: string;
    tool: ToolName;
    payload: Record<string, unknown>;
  };
}
