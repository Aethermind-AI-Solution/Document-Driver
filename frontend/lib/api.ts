const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const TOKEN_KEY = "aethermind_token";

export function getToken() { return typeof window !== "undefined" ? window.localStorage.getItem(TOKEN_KEY) || "" : ""; }
export function setToken(t: string) { if (typeof window !== "undefined") window.localStorage.setItem(TOKEN_KEY, t); }
export function clearToken() { if (typeof window !== "undefined") window.localStorage.removeItem(TOKEN_KEY); }

export async function api(path: string, init: RequestInit = {}) {
  const token = getToken();
  const headers: Record<string, string> = { ...(init.headers as Record<string, string> || {}) };
  if (token) headers["X-Access-Token"] = token;
  const r = await fetch(`${API}${path}`, { ...init, headers });
  if (r.status === 401) { clearToken(); const e: any = new Error("Unauthorized"); e.status = 401; throw e; }
  if (!r.ok) { const e: any = new Error(await r.text()); e.status = r.status; throw e; }
  return r.json();
}
