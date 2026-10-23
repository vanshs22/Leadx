"use client";

import { funnelShades } from "@/lib/chartColors";

export function FunnelChart({
  stages,
}: {
  stages?: { stage: string; count: number; slug?: string }[] | null;
}) {
  const list = Array.isArray(stages) ? stages : [];
  if (!list.length) {
    return <p className="text-sm text-ink-faint">No pipeline stages yet</p>;
  }
  const max = Math.max(1, ...list.map((s) => Number(s.count) || 0));
  return (
    <div className="space-y-2">
      {list.map((s, i) => {
        const pct = Math.max(6, Math.round(((Number(s.count) || 0) / max) * 100));
        const color = funnelShades[i % funnelShades.length];
        return (
          <div key={s.slug || s.stage || i} className="group">
            <div
              className="flex items-center justify-between rounded-lg px-3 py-2.5 text-sm font-medium text-white transition-all"
              style={{ width: `${pct}%`, minWidth: "140px", background: color }}
            >
              <span className="truncate">{s.stage}</span>
              <span className="font-mono tabular-nums">{s.count}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
