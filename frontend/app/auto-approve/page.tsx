"use client";
import { useEffect, useState } from "react";
import { api, me } from "../../lib/api";
import ConfirmDialog from "../components/ConfirmDialog";

type Config = { id: number; document_type: string; enabled: boolean; min_confidence: number; created_at: string };
type GroundedButWrong = { n_high_conf: number; n_wrong: number; rate: number | null };
type EvalReport = { n: number; header: string; per_field: unknown; reliability: unknown; grounded_but_wrong: GroundedButWrong };

// UI-only guardrail for this slice: the backend does not yet enforce a minimum
// sample size before allowing enable, so we block it here.
const MIN_N = 30;

// TODO(fast-follow): show global AUTO_APPROVE_ENABLED banner once the backend exposes it

export default function AutoApprovePage() {
  const [authorized, setAuthorized] = useState(false);
  const [configs, setConfigs] = useState<Config[]>([]);
  const [evals, setEvals] = useState<Record<string, EvalReport>>({});
  const [form, setForm] = useState({ document_type: "", min_confidence: "0.95" });
  const [pendingDelete, setPendingDelete] = useState<Config | null>(null);
  const [pendingEnable, setPendingEnable] = useState<Config | null>(null);

  const load = () =>
    api("/auto-approve").then(async (list: Config[]) => {
      setConfigs(list);
      const entries = await Promise.all(
        list.map((c) =>
          api(`/auto-approve/eval/${c.document_type}`)
            .then((r) => [c.document_type, r] as const)
            .catch(() => [c.document_type, null] as const)
        )
      );
      setEvals(Object.fromEntries(entries.filter(([, r]) => r)) as Record<string, EvalReport>);
    }).catch(() => (window.location.href = "/login"));

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
    await api("/auto-approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document_type: form.document_type, min_confidence: parseFloat(form.min_confidence) }),
    });
    setForm({ document_type: "", min_confidence: "0.95" });
    load();
  }

  async function patch(c: Config, body: Partial<{ enabled: boolean; min_confidence: number }>) {
    await api(`/auto-approve/${c.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    load();
  }

  async function remove(c: Config) {
    await api(`/auto-approve/${c.id}`, { method: "DELETE" });
    load();
  }

  if (!authorized) return null;
  return (
    <div className="mx-auto mt-10 max-w-3xl p-6">
      <h1 className="mb-2 text-xl font-semibold">Auto-approve</h1>
      <p className="mb-6 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
        Auto-approve finalizes a document and pushes it downstream with <strong>no human in the loop</strong>. Enabling
        it also blinds reviewers to mistakes they would otherwise catch during manual review. Only enable a document
        type once its evaluation evidence supports it.
      </p>

      <form onSubmit={add} className="mb-6 flex flex-wrap gap-2">
        <input aria-label="Document type" placeholder="document type" value={form.document_type}
               onChange={(e) => setForm({ ...form, document_type: e.target.value })} className="rounded border p-2" />
        <input aria-label="Minimum confidence" type="number" step="0.01" min="0.9" max="1" value={form.min_confidence}
               onChange={(e) => setForm({ ...form, min_confidence: e.target.value })} className="w-32 rounded border p-2" />
        <button className="rounded bg-black px-3 text-white">Add</button>
      </form>

      <ul className="space-y-4">
        {configs.map((c) => {
          const report = evals[c.document_type];
          const insufficient = !report || report.n < MIN_N;
          const disableEnable = !c.enabled && insufficient;
          const ratePct = report?.grounded_but_wrong?.rate != null ? `${Math.round(report.grounded_but_wrong.rate * 100)}%` : "—";
          return (
            <li key={c.id} className="rounded border p-4 text-sm">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <span className="font-semibold">{c.document_type}</span>
                <span className="flex items-center gap-2">
                  <label className="flex items-center gap-1 text-xs">
                    <input type="checkbox" aria-label={`Enable auto-approve for ${c.document_type}`}
                           checked={c.enabled} disabled={disableEnable}
                           onChange={(e) => { if (e.target.checked) setPendingEnable(c); else patch(c, { enabled: false }); }} />
                    Enabled
                  </label>
                  <input aria-label={`Minimum confidence for ${c.document_type}`} type="number" step="0.01" min="0.9" max="1"
                         defaultValue={c.min_confidence}
                         onBlur={(e) => {
                           const v = parseFloat(e.target.value);
                           if (!Number.isNaN(v) && v !== c.min_confidence) patch(c, { min_confidence: v });
                         }}
                         className="w-24 rounded border p-1" />
                  <button onClick={() => setPendingDelete(c)} className="rounded border px-2 py-1 text-xs">Delete</button>
                </span>
              </div>
              {disableEnable && <p className="text-xs font-semibold text-rose-600">insufficient data — do not enable</p>}
              <div className="rounded bg-slate-50 p-2 text-xs text-slate-600">
                {report ? (
                  <>
                    <p>Sample size (n): {report.n}</p>
                    <p>Grounded-but-wrong rate: {ratePct}</p>
                    <p className="mt-1 text-slate-500">{report.header}</p>
                  </>
                ) : (
                  <p>Loading safety snapshot…</p>
                )}
              </div>
            </li>
          );
        })}
        {!configs.length && <p className="text-sm text-slate-500">No auto-approve configs yet.</p>}
      </ul>

      <ConfirmDialog open={!!pendingEnable} title="Enable auto-approve?"
        message={pendingEnable
          ? `Enabling auto-approve for "${pendingEnable.document_type}" finalizes matching documents and pushes them downstream with no human review. You will not see the errors this would otherwise catch.`
          : ""}
        confirmLabel="Enable" onCancel={() => setPendingEnable(null)}
        onConfirm={() => { const c = pendingEnable; setPendingEnable(null); if (c) patch(c, { enabled: true }); }} />

      <ConfirmDialog open={!!pendingDelete} destructive title="Delete auto-approve config?"
        message={pendingDelete ? `Delete the auto-approve config for ${pendingDelete.document_type}?` : ""}
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => { const c = pendingDelete; setPendingDelete(null); if (c) remove(c); }} />
    </div>
  );
}
