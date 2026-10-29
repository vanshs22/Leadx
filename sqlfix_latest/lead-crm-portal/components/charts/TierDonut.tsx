"use client";

/**
 * Tier mix donut drawn as plain SVG arcs. Segment colours resolve through
 * `currentColor` + token classes: brass for Tier A (the opportunity signal),
 * navy for Tier B, light navy for Tier C. No literal colours, dark-mode safe.
 */
const SEGMENTS = [
  { key: "a", name: "Tier A", tone: "text-signal-500 dark:text-signal-300", dot: "bg-signal-500 dark:bg-signal-300" },
  { key: "b", name: "Tier B", tone: "text-brand-600 dark:text-brand-300", dot: "bg-brand-600 dark:bg-brand-300" },
  { key: "c", name: "Tier C", tone: "text-brand-200 dark:text-brand-700", dot: "bg-brand-200 dark:bg-brand-700" },
] as const;

const SIZE = 160;
const STROKE = 16;
const RADIUS = (SIZE - STROKE) / 2;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export function TierDonut({ a, b, c }: { a: number; b: number; c: number }) {
  const values = { a: Number(a) || 0, b: Number(b) || 0, c: Number(c) || 0 };
  const total = values.a + values.b + values.c;
  const gap = total > 0 && [values.a, values.b, values.c].filter((v) => v > 0).length > 1 ? 3 : 0;
  let offset = 0;
  const arcs = SEGMENTS.map((segment) => {
    const value = values[segment.key];
    const length = total > 0 ? (value / total) * CIRCUMFERENCE : 0;
    const arc = { ...segment, value, dash: Math.max(0, length - gap), offset };
    offset += length;
    return arc;
  });
  const summary = total
    ? `Tier mix: ${arcs.map((arc) => `${arc.name} ${arc.value}`).join(", ")} of ${total} leads`
    : "Tier mix: no leads yet";

  return (
    <div className="flex flex-wrap items-center gap-6">
      <div className="relative h-40 w-40 shrink-0">
        <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="h-full w-full -rotate-90" role="img" aria-label={summary}>
          <circle cx={SIZE / 2} cy={SIZE / 2} r={RADIUS} fill="none" stroke="currentColor" strokeWidth={STROKE} className="text-surface-3" />
          {arcs.map((arc) =>
            arc.dash > 0 ? (
              <circle
                key={arc.key}
                cx={SIZE / 2}
                cy={SIZE / 2}
                r={RADIUS}
                fill="none"
                stroke="currentColor"
                strokeWidth={STROKE}
                strokeDasharray={`${arc.dash} ${CIRCUMFERENCE - arc.dash}`}
                strokeDashoffset={-arc.offset}
                className={arc.tone}
              />
            ) : null,
          )}
        </svg>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center" aria-hidden="true">
          <span className="font-display text-2xl font-semibold tabular-nums text-ink">{total.toLocaleString()}</span>
          <span className="text-xs text-ink-faint">leads</span>
        </div>
      </div>
      <ul className="min-w-[140px] flex-1 space-y-2.5 text-[0.8125rem]" aria-label="Tier legend">
        {arcs.map((arc) => (
          <li key={arc.key} className="flex items-center justify-between gap-4">
            <span className="flex items-center gap-2 text-ink-muted">
              <span className={`h-2.5 w-2.5 rounded-sm ${arc.dot}`} aria-hidden="true" />
              {arc.name}
            </span>
            <span className="tabular-nums text-ink">
              <span className="font-semibold">{arc.value.toLocaleString()}</span>
              <span className="ml-2 text-xs text-ink-faint">{total ? Math.round((arc.value / total) * 100) : 0}%</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
