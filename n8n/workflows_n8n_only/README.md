# n8n-Only Consistent Bot Setup

## Workflow file
- `wf_telegram_appointment_n8n_only.json`

## What this fixes from your current setup
1. Prompt-driven randomness removed.
   - Old setup depended on a long LLM prompt + tool calls.
   - New setup uses deterministic Code-node state machine.

2. Contradictory rules removed.
   - Old setup had conflicting slot/date/hour rules across prompt and tools.
   - New setup has one source of truth in `State Machine Engine`.

3. Slot checks made explicit.
   - Old setup could check calendar with ambiguous filters.
   - New setup always passes `timeMin` and `timeMax` from generated candidate slots.

4. Confirmation gate enforced.
   - Event creation only happens after explicit yes/confirm.

5. Duplicate message handling added.
   - De-dup key: `chatId:messageId` in workflow static storage.

6. Consistent fallback on failures.
   - Calendar create/delete failures are queued in deadletter and user gets deterministic fallback.

7. Non-text message behavior standardized.
   - Non-text Telegram updates trigger fixed fallback reply.

8. Google Sheets consistency improved.
   - Booking rows keyed by `Appointment ID` (idempotency key), not only chat id.

## Required credentials
Replace placeholders before activation:
- `__REPLACE_TELEGRAM_CREDENTIAL_ID__`
- `__REPLACE_GCAL_CREDENTIAL_ID__`
- `__REPLACE_GSHEETS_CREDENTIAL_ID__`

## Required environment variables in n8n
- `GCAL_CALENDAR_ID`
- `GSHEET_DOCUMENT_ID`
- `GSHEET_SHEET_NAME`

## Deployment checklist
1. Import workflow JSON.
2. Replace credential IDs.
3. Set environment variables.
4. Keep old workflow disabled.
5. Test these flows in sequence:
   - new booking (name -> mobile -> date -> period -> slot -> confirm)
   - no slot available in selected period
   - same-day/past-date rejection
   - cancel flow
   - reschedule flow
   - duplicate message suppression
   - calendar API failure fallback

## Known n8n-only limitation
- Session/deadletter state is stored in workflow static data.
- This is deterministic but not ideal for horizontal scaling/multi-instance workers.
- If you later scale n8n workers, move state/deadletter to a shared DB/Data Store node.
