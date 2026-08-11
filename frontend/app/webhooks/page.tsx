"use client";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";

type Hook = { id: number; document_type: string; url: string; active: boolean; has_secret: boolean };

export default function WebhooksPage() {
  const [hooks, setHooks] = useState<Hook[]>([]);
  const [form, setForm] = useState({ document_type: "", url: "", secret: "" });
  const load = () => api("/webhooks").then(setHooks).catch(() => (window.location.href = "/login"));
  useEffect(() => { load(); }, []);
  async function add(e: React.FormEvent) {
    e.preventDefault();
    await api("/webhooks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(form) });
    setForm({ document_type: "", url: "", secret: "" }); load();
  }
  async function toggle(h: Hook) {
    await api(`/webhooks/${h.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ active: !h.active }) });
    load();
  }
  async function remove(h: Hook) {
    await api(`/webhooks/${h.id}`, { method: "DELETE" }); load();
  }
  return (
    <div className="mx-auto mt-10 max-w-2xl p-6">
      <h1 className="mb-4 text-xl font-semibold">Webhooks</h1>
      <form onSubmit={add} className="mb-6 flex flex-wrap gap-2">
        <input aria-label="Document type" placeholder="document type" value={form.document_type}
               onChange={(e) => setForm({ ...form, document_type: e.target.value })} className="rounded border p-2" />
        <input aria-label="URL" placeholder="https://…" value={form.url}
               onChange={(e) => setForm({ ...form, url: e.target.value })} className="flex-1 rounded border p-2" />
        <input aria-label="Secret" placeholder="signing secret (optional)" value={form.secret}
               onChange={(e) => setForm({ ...form, secret: e.target.value })} className="rounded border p-2" />
        <button className="rounded bg-black px-3 text-white">Add</button>
      </form>
      <ul className="space-y-2">
        {hooks.map((h) => (
          <li key={h.id} className="flex items-center justify-between rounded border p-3 text-sm">
            <span>{h.document_type} → {h.url}{h.has_secret ? " 🔒" : ""}{h.active ? "" : " (inactive)"}</span>
            <span className="flex gap-2">
              <button onClick={() => toggle(h)} className="rounded border px-2 py-1">{h.active ? "Disable" : "Enable"}</button>
              <button onClick={() => remove(h)} className="rounded border px-2 py-1">Delete</button>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
