"use client";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { computeRoi } from "../../lib/roi";

export default function RoiPage() {
  const [roi, setRoi] = useState<{ stp_rate: number | null; avg_review_seconds: number | null } | null>(null);
  const [volume, setVolume] = useState(50000);
  const [cost, setCost] = useState(2);
  const [minutes, setMinutes] = useState(4);
  useEffect(() => { api("/admin/roi").then(setRoi).catch(() => setRoi(null)); }, []);
  const stp = roi?.stp_rate ?? 0;
  const calc = computeRoi({ monthlyVolume: volume, costPerDoc: cost, minutesPerDoc: minutes, stpRate: stp });
  return (
    <main className="mx-auto max-w-3xl p-6">
      <h1 className="text-2xl font-semibold">ROI</h1>
      <div className="mt-4 grid grid-cols-2 gap-3">
        <div className="card p-4"><p className="label">Straight-through rate</p>
          <p className="mt-1 text-2xl font-semibold">{roi?.stp_rate != null ? `${Math.round(roi.stp_rate * 100)}%` : "—"}</p></div>
        <div className="card p-4"><p className="label">Avg. handling time</p>
          <p className="mt-1 text-2xl font-semibold">{roi?.avg_review_seconds != null ? `${roi.avg_review_seconds}s` : "—"}</p></div>
      </div>
      <div className="card mt-4 p-4">
        <p className="label mb-2">Your numbers</p>
        <label className="block text-sm">Monthly volume
          <input type="number" value={volume} onChange={e => setVolume(+e.target.value)} className="mt-1 w-full rounded border border-slate-200 px-2 py-1.5" /></label>
        <label className="mt-2 block text-sm">Cost per doc ($)
          <input type="number" value={cost} onChange={e => setCost(+e.target.value)} className="mt-1 w-full rounded border border-slate-200 px-2 py-1.5" /></label>
        <label className="mt-2 block text-sm">Minutes per doc
          <input type="number" value={minutes} onChange={e => setMinutes(+e.target.value)} className="mt-1 w-full rounded border border-slate-200 px-2 py-1.5" /></label>
      </div>
      <div className="card mt-4 p-4">
        <p className="label">Estimated monthly saving (from your inputs × STP)</p>
        <p className="mt-1 text-3xl font-semibold">${calc.monthlySaved.toLocaleString()}</p>
        <p className="mt-1 text-sm text-slate-500">{calc.savedDocs.toLocaleString()} docs auto-handled · {calc.hoursSaved.toLocaleString()} hours saved</p>
      </div>
    </main>
  );
}
