// frontend/lib/redact.ts
export function maskValue(value: string, isPii: boolean, hide: boolean): string {
  return isPii && hide && value ? "••••" : value;
}
