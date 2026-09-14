export function renderAnomaly(a: string | { message?: string; type?: string; duplicate_of?: number }): string {
  return typeof a === "string" ? a : (a?.message || "Anomaly");
}
