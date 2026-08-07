-- Enterprise Booking Core: Initial schema

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS patients (
  id UUID PRIMARY KEY,
  telegram_chat_id TEXT NOT NULL UNIQUE,
  full_name TEXT NOT NULL,
  mobile TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sessions (
  id UUID PRIMARY KEY,
  chat_id TEXT NOT NULL,
  state TEXT NOT NULL,
  data JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL DEFAULT 'active',
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sessions_chat_status ON sessions (chat_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_last_correlation ON sessions ((data->>'lastCorrelationId'));

CREATE TABLE IF NOT EXISTS appointments (
  id UUID PRIMARY KEY,
  patient_id UUID NOT NULL REFERENCES patients(id),
  chat_id TEXT NOT NULL,
  doctor_id TEXT NOT NULL,
  start_at TIMESTAMPTZ NOT NULL,
  end_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL,
  calendar_event_id TEXT UNIQUE,
  idempotency_key TEXT NOT NULL UNIQUE,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_appointments_chat ON appointments (chat_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_appointments_status ON appointments (status);

CREATE UNIQUE INDEX IF NOT EXISTS uq_appointments_active_slot
ON appointments (doctor_id, start_at)
WHERE status IN ('confirmed', 'held');

CREATE TABLE IF NOT EXISTS inbound_messages (
  id UUID PRIMARY KEY,
  channel TEXT NOT NULL,
  chat_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  text TEXT,
  raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  correlation_id TEXT NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(channel, chat_id, message_id)
);

CREATE TABLE IF NOT EXISTS tool_runs (
  id UUID PRIMARY KEY,
  correlation_id TEXT NOT NULL,
  session_id UUID NOT NULL REFERENCES sessions(id),
  tool_name TEXT NOT NULL,
  status TEXT NOT NULL,
  request_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  response_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tool_runs_correlation ON tool_runs (correlation_id);

CREATE TABLE IF NOT EXISTS audit_events (
  id UUID PRIMARY KEY,
  correlation_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_events_correlation ON audit_events (correlation_id, created_at DESC);

CREATE TABLE IF NOT EXISTS outbox_messages (
  id UUID PRIMARY KEY,
  correlation_id TEXT NOT NULL,
  channel TEXT NOT NULL,
  chat_id TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL,
  retry_count INT NOT NULL DEFAULT 0,
  next_retry_at TIMESTAMPTZ,
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_outbox_status_next_retry ON outbox_messages (status, next_retry_at);

CREATE OR REPLACE FUNCTION set_updated_at_timestamp()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_patients_updated_at ON patients;
CREATE TRIGGER trg_patients_updated_at
BEFORE UPDATE ON patients
FOR EACH ROW
EXECUTE FUNCTION set_updated_at_timestamp();

DROP TRIGGER IF EXISTS trg_sessions_updated_at ON sessions;
CREATE TRIGGER trg_sessions_updated_at
BEFORE UPDATE ON sessions
FOR EACH ROW
EXECUTE FUNCTION set_updated_at_timestamp();

DROP TRIGGER IF EXISTS trg_appointments_updated_at ON appointments;
CREATE TRIGGER trg_appointments_updated_at
BEFORE UPDATE ON appointments
FOR EACH ROW
EXECUTE FUNCTION set_updated_at_timestamp();

DROP TRIGGER IF EXISTS trg_outbox_messages_updated_at ON outbox_messages;
CREATE TRIGGER trg_outbox_messages_updated_at
BEFORE UPDATE ON outbox_messages
FOR EACH ROW
EXECUTE FUNCTION set_updated_at_timestamp();
