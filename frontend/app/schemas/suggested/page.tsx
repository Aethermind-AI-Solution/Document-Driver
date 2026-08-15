"use client";
import { useEffect, useState } from "react";
import { api, me } from "../../../lib/api";

type Field = { name: string; label: string; type: string; required: boolean };
type Draft = { id: number; key: string; name: string; fields: Field[]; origin_document_id: number | null; created_at: string | null };
const TYPES = ["string", "number", "date", "array"];

export default function SuggestedSchemasPage() {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [authorized, setAuthorized] = useState(false);
  const load = () => api("/schemas/suggested").then(setDrafts).catch(() => (window.location.href = "/login"));
  useEffect(() => {
    me().then((u: any) => {
      if (u.role !== "admin") { window.location.href = "/"; return; }
      setAuthorized(true); load();
    }).catch(() => (window.location.href = "/login"));
  }, []);

  function setField(d: Draft, i: number, patch: Partial<Field>) {
    setDrafts((ds) => ds.map((x) => x.id !== d.id ? x
      : { ...x, fields: x.fields.map((f, j) => (j === i ? { ...f, ...patch } : f)) }));
  }
  function addField(d: Draft) {
    setDrafts((ds) => ds.map((x) => x.id !== d.id ? x
      : { ...x, fields: [...x.fields, { name: "new_field", label: "New Field", type: "string", required: false }] }));
  }
  function removeField(d: Draft, i: number) {
    setDrafts((ds) => ds.map((x) => x.id !== d.id ? x : { ...x, fields: x.fields.filter((_, j) => j !== i) }));
  }
  async function save(d: Draft) {
    await api(`/schemas/${d.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: d.name, fields: d.fields }) });
    load();
  }
  async function approve(d: Draft) { await api(`/schemas/${d.id}/approve`, { method: "POST" }); load(); }
  async function reject(d: Draft) { await api(`/schemas/${d.id}`, { method: "DELETE" }); load(); }

  if (!authorized) return null;
  return (
    <div className="mx-auto mt-10 max-w-3xl p-6">
      <h1 className="mb-4 text-xl font-semibold">Suggested Schemas</h1>
      {drafts.length === 0 && <p className="text-sm text-slate-500">No suggested schemas.</p>}
      <ul className="space-y-6">
        {drafts.map((d) => (
          <li key={d.id} className="rounded border p-4">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm font-semibold">{d.name} <code className="text-xs text-slate-500">({d.key})</code></span>
              <span className="text-xs text-slate-500">from doc #{d.origin_document_id ?? "—"}</span>
            </div>
            <ul className="mb-3 space-y-1">
              {d.fields.map((f, i) => (
                <li key={i} className="flex flex-wrap items-center gap-2 text-sm">
                  <input aria-label="Field label" value={f.label}
                         onChange={(e) => setField(d, i, { label: e.target.value })} className="rounded border p-1" />
                  <code className="text-xs text-slate-500">{f.name}</code>
                  <select aria-label="Field type" value={f.type}
                          onChange={(e) => setField(d, i, { type: e.target.value })} className="rounded border p-1">
                    {TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                  <label className="flex items-center gap-1 text-xs">
                    <input type="checkbox" checked={f.required}
                           onChange={(e) => setField(d, i, { required: e.target.checked })} /> required
                  </label>
                  <button onClick={() => removeField(d, i)} className="text-xs text-rose-600 hover:underline">remove</button>
                </li>
              ))}
            </ul>
            <div className="flex gap-2">
              <button onClick={() => addField(d)} className="rounded border px-2 py-1 text-xs">Add field</button>
              <button onClick={() => save(d)} className="rounded border px-2 py-1 text-xs">Save</button>
              <button onClick={() => approve(d)} className="rounded bg-black px-3 py-1 text-xs text-white">Approve</button>
              <button onClick={() => reject(d)} className="rounded border border-rose-300 px-2 py-1 text-xs text-rose-600">Reject</button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
