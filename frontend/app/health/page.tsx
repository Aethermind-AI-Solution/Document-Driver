"use client";
import { useEffect, useState } from "react";
import { api, me } from "../../lib/api";

type Metrics = {
  status_counts: Record<string, number>;
  errors_recent: { id: number; filename: string; document_type: string; detail: string | null; timestamp: string }[];
  stuck_processing: { threshold_minutes: number; count: number; documents: { id: number; filename: string; minutes: number }[] };
  stage_latency: { stage: string; p50_ms: number | null; p95_ms: number | null; n: number }[];
  sample_size: number;
};

export default function HealthPage() {
  const [m, setM] = useState<Metrics | null>(null);
  const [authorized, setAuthorized] = useState(false);
  const load = () => api("/admin/metrics").then(setM).catch(() => (window.location.href = "/login"));
  useEffect(() => {
    me().then((u: any) => {
      if (u.role !== "admin") { window.location.href = "/"; return; }
      setAuthorized(true); load();
    }).catch(() => (window.location.href = "/login"));
  }, []);
  if (!authorized || !m) return null;

  const stuck = m.stuck_processing.count;
  const errs = m.errors_recent.length;
  const banner = stuck > 0
    ? { cls: "bg-rose-50 text-rose-700", text: `${stuck} document(s) stuck in processing` }
    : errs > 0
    ? { cls: "bg-amber-50 text-amber-700", text: `${errs} recent processing error(s)` }
    : { cls: "bg-emerald-50 text-emerald-700", text: "All systems nominal" };

  return (
    <div className="mx-auto mt-10 max-w-3xl p-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold">System Health</h1>
        <button onClick={load} className="text-sm font-semibold text-slate-700">Refresh</button>
      </div>
      <div className={`mb-6 rounded-lg px-4 py-3 text-sm font-semibold ${banner.cls}`}>{banner.text}</div>

      <h2 className="mb-2 text-sm font-semibold text-slate-500">Documents by status</h2>
      <div className="mb-6 flex flex-wrap gap-2">
        {Object.entries(m.status_counts).map(([s, n]) => (
          <span key={s} className="rounded-full border border-slate-200 px-2.5 py-1 text-xs">
            {s.replaceAll("_", " ")}: <span className="font-semibold tabular-nums">{n}</span>
          </span>
        ))}
      </div>

      {stuck > 0 && (
        <div className="mb-6">
          <h2 className="mb-2 text-sm font-semibold text-slate-500">Stuck in processing</h2>
          <ul className="space-y-1 text-sm">
            {m.stuck_processing.documents.map((d) => (
              <li key={d.id}>{d.filename} — <span className="tabular-nums">{d.minutes}m</span></li>
            ))}
          </ul>
        </div>
      )}

      <h2 className="mb-2 text-sm font-semibold text-slate-500">Recent errors</h2>
      {m.errors_recent.length === 0 ? (
        <p className="mb-6 text-sm text-slate-500">None.</p>
      ) : (
        <ul className="mb-6 space-y-2 text-sm">
          {m.errors_recent.map((e) => (
            <li key={e.id} className="rounded border border-slate-200 p-2">
              <span className="font-medium">{e.filename}</span> <span className="text-xs text-slate-400">({e.document_type})</span>
              {e.detail && <p className="text-slate-500">{e.detail}</p>}
            </li>
          ))}
        </ul>
      )}

      <h2 className="mb-2 text-sm font-semibold text-slate-500">Pipeline stage latency <span className="font-normal text-slate-400">(last {m.sample_size})</span></h2>
      <div className="overflow-x-auto rounded border border-slate-200">
        <table className="w-full border-collapse text-sm">
          <thead><tr className="text-left text-xs uppercase tracking-wide text-slate-400">
            <th className="px-3 py-1.5">Stage</th><th className="px-3 py-1.5">p50 ms</th><th className="px-3 py-1.5">p95 ms</th><th className="px-3 py-1.5">n</th>
          </tr></thead>
          <tbody>
            {m.stage_latency.map((s) => (
              <tr key={s.stage}>
                <td className="border-t border-slate-100 px-3 py-1.5">{s.stage}</td>
                <td className="border-t border-slate-100 px-3 py-1.5 tabular-nums">{s.p50_ms ?? "—"}</td>
                <td className="border-t border-slate-100 px-3 py-1.5 tabular-nums">{s.p95_ms ?? "—"}</td>
                <td className="border-t border-slate-100 px-3 py-1.5 tabular-nums">{s.n}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
