"use client";
type F = { field_name: string; box?: number[] | null };

export default function DocumentViewer({
  imageUrl, fields, activeField, onPickField,
}: { imageUrl: string; fields: F[]; activeField: string | null; onPickField: (name: string) => void }) {
  return (
    <div className="relative w-full overflow-auto rounded-lg border border-slate-200 bg-slate-50">
      {imageUrl ? <img src={imageUrl} alt="Document page" className="block w-full" /> : null}
      <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox="0 0 1 1" preserveAspectRatio="none">
        {fields.filter(f => Array.isArray(f.box) && f.box!.length === 4).map(f => {
          const [x0, y0, x1, y1] = f.box as number[];
          const active = f.field_name === activeField;
          return (
            <rect
              key={f.field_name}
              data-testid={`box-${f.field_name}`}
              data-active={active ? "true" : "false"}
              x={x0} y={y0} width={Math.max(0, x1 - x0)} height={Math.max(0, y1 - y0)}
              onClick={() => onPickField(f.field_name)}
              className="pointer-events-auto cursor-pointer"
              fill={active ? "rgba(16,185,129,0.25)" : "rgba(59,130,246,0.12)"}
              stroke={active ? "#10b981" : "#3b82f6"}
              strokeWidth={0.004}
            />
          );
        })}
      </svg>
    </div>
  );
}
