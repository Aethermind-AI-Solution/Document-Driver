import dayjs from "dayjs";
import customParseFormat from "dayjs/plugin/customParseFormat.js";
import utc from "dayjs/plugin/utc.js";
import timezone from "dayjs/plugin/timezone.js";

dayjs.extend(customParseFormat);
dayjs.extend(utc);
dayjs.extend(timezone);

export const IST = "Asia/Kolkata";

export function nowUtcIso(): string {
  return new Date().toISOString();
}

export function nowInTimezone(tz: string = IST): dayjs.Dayjs {
  return dayjs().tz(tz);
}

export function addMinutesUtc(isoUtc: string, minutes: number): string {
  return dayjs.utc(isoUtc).add(minutes, "minute").toISOString();
}

export function parseUserDate(text: string, tz: string = IST): dayjs.Dayjs | null {
  const cleaned = text.trim();
  const nowTz = nowInTimezone(tz);
  const formats = [
    "YYYY-MM-DD",
    "DD-MM-YYYY",
    "D-M-YYYY",
    "DD/MM/YYYY",
    "D/M/YYYY",
    "D MMM YYYY",
    "DD MMM YYYY",
    "D MMMM YYYY",
    "DD MMMM YYYY",
    "D MMM",
    "DD MMM",
    "D MMMM",
    "DD MMMM"
  ];

  for (const format of formats) {
    try {
      const parsedLocal = dayjs(cleaned, format, true);
      if (!parsedLocal.isValid()) {
        continue;
      }

      const adjusted = format.includes("YYYY") ? parsedLocal : parsedLocal.year(nowTz.year());
      return dayjs.tz(adjusted.format("YYYY-MM-DD"), "YYYY-MM-DD", tz).startOf("day");
    } catch {
      continue;
    }
  }

  try {
    const natural = dayjs(cleaned);
    if (natural.isValid()) {
      return dayjs.tz(natural.format("YYYY-MM-DD"), "YYYY-MM-DD", tz).startOf("day");
    }
  } catch {
    return null;
  }

  return null;
}

export function isPastOrSameDay(dateInTz: dayjs.Dayjs, tz: string = IST): boolean {
  const today = nowInTimezone(tz).startOf("day");
  return !dateInTz.isAfter(today, "day");
}

export function normalizePhone(text: string): string | null {
  const digits = text.replace(/\D/g, "");
  if (digits.length === 10) {
    return digits;
  }
  if (digits.length === 12 && digits.startsWith("91")) {
    return digits.slice(2);
  }
  return null;
}

export function hasDevanagari(text: string): boolean {
  return /[\u0900-\u097F]/.test(text);
}

export function toUtcFromTz(localDateTime: string, tz: string = IST): string {
  return dayjs.tz(localDateTime, tz).utc().toISOString();
}

export function toDisplayFromUtc(isoUtc: string, tz: string = IST): string {
  return dayjs.utc(isoUtc).tz(tz).format("DD MMM YYYY, hh:mm A");
}
