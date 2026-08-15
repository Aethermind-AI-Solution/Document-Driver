"use client";
import { useEffect } from "react";

export default function ConfirmDialog({
  open, title, message, confirmLabel = "Confirm", cancelLabel = "Cancel",
  destructive = false, onConfirm, onCancel,
}: {
  open: boolean; title: string; message?: string; confirmLabel?: string;
  cancelLabel?: string; destructive?: boolean; onConfirm: () => void; onCancel: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);
  if (!open) return null;
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="confirm-title"
         className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
         onClick={onCancel}>
      <div className="w-full max-w-sm rounded-xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <h2 id="confirm-title" className="text-lg font-semibold">{title}</h2>
        {message && <p className="mt-2 text-sm text-slate-600">{message}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onCancel}
                  className="rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold hover:bg-slate-50">
            {cancelLabel}
          </button>
          <button onClick={onConfirm}
                  className={`rounded-lg px-3 py-2 text-sm font-semibold text-white ${destructive ? "bg-rose-600 hover:bg-rose-700" : "bg-slate-900 hover:bg-slate-800"}`}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
