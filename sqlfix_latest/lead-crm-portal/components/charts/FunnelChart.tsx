"use client";

/**
 * Stage funnel as labelled horizontal bars. Colours come from the navy ink
 * scale via token classes (so dark mode follows the theme); the final stage
 * reads as emerald when it is the furthest-down step of a multi-stage funnel.
 */
const STAGE_FILL = [
  "bg-brand-300 dark:bg-brand-600",
  "bg-brand-400 dark:bg-brand-500",
  "bg-brand-500 dark:bg-brand-400",
  "bg-brand-600 dark:bg-brand-300",
  "bg-brand-700 dark:bg-brand-200",
];

export function FunnelChart({
  stages,
}: {
  stages?: { stage: string; count: number; slug?: string }[] | null;
}) {
  const list = Array.isArray(stages) ? stages : [];
  if (!list.length) {
    return <p className="py-6 text-center text-[0.8125rem] text-ink-faint">No pipeline stages yet</p>;
  }
  const max = Math.max(1, ...list.map((s) => Number(s.count) || 0));
  const top = Math.max(1, Number(list[0]?.count) || 0);
  return (
    <ol className="space-y-3" aria-label="Pipeline funnel by stage">
      {list.map((s, i) => {
        const count = Number(s.count) || 0;
        const pct = count > 0 ? Math.max(3, Math.round((count / max) * 100)) : 0;
        const share = Math.round((count / top) * 100);
        const isLast = list.length > 2 && i === list.length - 1;
        const fill = isLast ? "bg-emerald-600 dark:bg-emerald-400" : STAGE_FILL[i % STAGE_FILL.length];
        return (
          <li key={s.slug || s.stage || i} className="min-w-0">
            <div className="flex items-baseline justify-between gap-3 text-[0.8125rem]">
              <span className="truncate font-medium text-ink">{s.stage}</span>
              <span className="shrink-0 tabular-nums text-ink">
                <span className="font-semibold">{count.toLocaleString()}</span>
                {i > 0 ? <span className="ml-2 text-xs text-ink-faint">{share}%</span> : null}
              </span>
            </div>
            <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-surface-3" aria-hidden="true">
              <div className={`h-full rounded-full ${fill}`} style={{ width: `${pct}%` }} />
            </div>
          </li>
        );
      })}
    </ol>
  );
}
