"use client";
import { useEffect, useState } from "react";
import { api, me } from "../../lib/api";
import type { User } from "../../lib/auth";

export default function UsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [form, setForm] = useState({ email: "", password: "", role: "reviewer" });
  const [authorized, setAuthorized] = useState(false);
  const load = () => api("/users").then(setUsers).catch(() => (window.location.href = "/login"));
  useEffect(() => {
    me()
      .then((u: any) => {
        if (u.role !== "admin") { window.location.href = "/"; return; }
        setAuthorized(true);
        load();
      })
      .catch(() => (window.location.href = "/login"));
  }, []);
  async function add(e: React.FormEvent) {
    e.preventDefault();
    await api("/users", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(form) });
    setForm({ email: "", password: "", role: "reviewer" });
    load();
  }
  async function deactivate(id: number) {
    await api(`/users/${id}?is_active=false`, { method: "PATCH" });
    load();
  }
  if (!authorized) return null;
  return (
    <div className="mx-auto mt-10 max-w-2xl p-6">
      <h1 className="mb-4 text-xl font-semibold">Users</h1>
      <form onSubmit={add} className="mb-6 flex gap-2">
        <input aria-label="Email" placeholder="email" value={form.email}
               onChange={(e) => setForm({ ...form, email: e.target.value })} className="rounded border p-2" />
        <input aria-label="Password" placeholder="password" type="password" value={form.password}
               onChange={(e) => setForm({ ...form, password: e.target.value })} className="rounded border p-2" />
        <select aria-label="Role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}
                className="rounded border p-2">
          <option value="admin">admin</option><option value="reviewer">reviewer</option><option value="viewer">viewer</option>
        </select>
        <button className="rounded bg-black px-3 text-white">Add</button>
      </form>
      <ul>
        {users.map((u) => (
          <li key={u.id} className="flex items-center justify-between border-b border-slate-100 py-2 text-sm">
            <span>{u.email} — {u.role}{u.is_active ? "" : " (inactive)"}</span>
            {u.is_active && (
              <button onClick={() => deactivate(u.id)} className="text-xs font-semibold text-rose-600 hover:underline">
                Deactivate
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
