export type HighLevelIntent = "BOOK" | "CANCEL" | "RESCHEDULE" | "UNKNOWN";

function normalizeResponse(text: string): string {
  return text.trim().toLowerCase().replace(/[!?.,]+/g, "").replace(/\s+/g, " ");
}

function formatLatinNamePart(part: string): string {
  return part
    .split(/([-'’])/)
    .map((segment) => {
      if (!segment || /[-'’]/.test(segment)) {
        return segment;
      }
      return segment.charAt(0).toUpperCase() + segment.slice(1).toLowerCase();
    })
    .join("");
}

export function detectIntent(text: string): HighLevelIntent {
  const value = text.toLowerCase();
  if (/(cancel|cancellation|radd|band|रद्द|कैंसल)/u.test(value)) {
    return "CANCEL";
  }
  if (/(reschedule|change|shift|move|रीशेड्यूल|बदल)/u.test(value)) {
    return "RESCHEDULE";
  }
  if (/(book|appointment|visit|slot|doctor|consult|बुक|अपॉइंटमेंट|डॉक्टर)/u.test(value)) {
    return "BOOK";
  }
  return "UNKNOWN";
}

export function isConfirmation(text: string): boolean {
  return new Set(["yes", "confirm", "ok", "okay", "haan", "ha", "kar do", "confirm karo", "हाँ", "हां", "जी", "जी हाँ", "जी हां"]).has(
    normalizeResponse(text)
  );
}

export function isNegative(text: string): boolean {
  return new Set(["no", "nah", "nahi", "cancel", "नहीं", "नही", "ना", "मत करो"]).has(normalizeResponse(text));
}

export function detectPeriod(text: string): "MORNING" | "EVENING" | null {
  const value = text.toLowerCase();
  if (/(morning|subah|am|सुबह)/u.test(value)) {
    return "MORNING";
  }
  if (/(evening|afternoon|shaam|pm|raat|शाम|दोपहर|रात)/u.test(value)) {
    return "EVENING";
  }
  return null;
}

export function extractName(text: string): string | null {
  const cleaned = text
    .replace(/[^\p{L}\p{M}\s'’-]/gu, " ")
    .trim()
    .replace(/\s+/g, " ");
  if (cleaned.length < 2) {
    return null;
  }
  return cleaned
    .split(" ")
    .map((part) => (/\p{Script=Latin}/u.test(part) ? formatLatinNamePart(part) : part))
    .join(" ");
}
