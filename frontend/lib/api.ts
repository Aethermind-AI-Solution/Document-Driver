const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const TOKEN_KEY = "aethermind_token";

export function getToken() { return typeof window !== "undefined" ? window.localStorage.getItem(TOKEN_KEY) || "" : ""; }
export function setToken(t: string) { if (typeof window !== "undefined") window.localStorage.setItem(TOKEN_KEY, t); }
export function clearToken() { if (typeof window !== "undefined") window.localStorage.removeItem(TOKEN_KEY); }

export async function api(path: string, init: RequestInit = {}) {
  const token = getToken();
  const headers: Record<string, string> = { ...(init.headers as Record<string, string> || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const r = await fetch(`${API}${path}`, { ...init, headers });
  if (r.status === 401) { clearToken(); const e: any = new Error("Unauthorized"); e.status = 401; throw e; }
  if (!r.ok) { const e: any = new Error(await r.text()); e.status = r.status; throw e; }
  return r.json();
}

export async function downloadFile(path: string, filename: string) {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const r = await fetch(`${API}${path}`, { headers });
  if (r.status === 401) { clearToken(); const e: any = new Error("Unauthorized"); e.status = 401; throw e; }
  if (!r.ok) { const e: any = new Error(await r.text()); e.status = r.status; throw e; }
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; document.body.appendChild(a); a.click();
  a.remove(); URL.revokeObjectURL(url);
}

export async function login(email: string, password: string) {
  const r = await fetch(`${API}/auth/login`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!r.ok) { const e: any = new Error("Login failed"); e.status = r.status; throw e; }
  const data = await r.json();
  setToken(data.access_token);
  return data;
}

export async function me() { return api("/auth/me"); }
