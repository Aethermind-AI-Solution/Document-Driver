import dayjs from "dayjs";
import utc from "dayjs/plugin/utc.js";
import timezone from "dayjs/plugin/timezone.js";
import { IST } from "../lib/time.js";
import { Period } from "./types.js";

dayjs.extend(utc);
dayjs.extend(timezone);

interface WindowDefinition {
  start: string;
  end: string;
}

const SLOTS_BY_DAY: Record<number, WindowDefinition[]> = {
  0: [{ start: "18:30", end: "21:30" }],
  1: [{ start: "18:30", end: "21:30" }],
  2: [{ start: "18:30", end: "21:30" }],
  3: [{ start: "18:30", end: "21:30" }],
  4: [{ start: "18:30", end: "21:30" }],
  5: [{ start: "18:30", end: "21:30" }],
  6: [
    { start: "10:30", end: "13:00" },
    { start: "18:30", end: "21:30" }
  ]
};

export interface SlotCandidate {
  startUtc: string;
  endUtc: string;
  display: string;
}

function includeForPeriod(time24: string, period: Period): boolean {
  const [hourRaw] = time24.split(":");
  const hour = Number(hourRaw);
  if (period === "MORNING") {
    return hour < 12;
  }
  return hour >= 12;
}

export function generateSlotCandidates(dateYmd: string, period: Period, slotMinutes = 10): SlotCandidate[] {
  const date = dayjs.tz(dateYmd, "YYYY-MM-DD", IST);
  if (!date.isValid()) {
    return [];
  }

  const day = date.day();
  const windows = SLOTS_BY_DAY[day] ?? [];
  const slots: SlotCandidate[] = [];

  for (const windowDef of windows) {
    let cursor = dayjs.tz(`${dateYmd} ${windowDef.start}`, "YYYY-MM-DD HH:mm", IST);
    const end = dayjs.tz(`${dateYmd} ${windowDef.end}`, "YYYY-MM-DD HH:mm", IST);

    while (!cursor.add(slotMinutes, "minute").isAfter(end)) {
      const localTime = cursor.format("HH:mm");
      if (includeForPeriod(localTime, period)) {
        const endLocal = cursor.add(slotMinutes, "minute");
        slots.push({
          startUtc: cursor.utc().toISOString(),
          endUtc: endLocal.utc().toISOString(),
          display: cursor.format("hh:mm A")
        });
      }
      cursor = cursor.add(slotMinutes, "minute");
    }
  }

  return slots;
}

export function selectFreeSlots(candidates: SlotCandidate[], busyStartUtc: string[], limit = 3): SlotCandidate[] {
  const busySet = new Set(busyStartUtc.map((slot) => dayjs.utc(slot).toISOString()));
  const available = candidates.filter((slot) => !busySet.has(dayjs.utc(slot.startUtc).toISOString()));
  return available.slice(0, limit);
}

export function parseSlotText(text: string): string | null {
  const match = text.match(/\b(1[0-2]|0?[1-9]):([0-5][0-9])\s?(AM|PM)\b/i);
  if (!match) {
    return null;
  }

  const hour = Number(match[1]);
  const minute = match[2];
  const meridiem = match[3].toUpperCase();
  const normalizedHour = hour.toString().padStart(2, "0");

  return `${normalizedHour}:${minute} ${meridiem}`;
}
