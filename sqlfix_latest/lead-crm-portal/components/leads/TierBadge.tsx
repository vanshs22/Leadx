"use client";

/*
 * Tier A is the one opportunity signal that earns brass. B reads as navy ink,
 * C as neutral, so a list of leads scans by strength without a rainbow.
 */
const TIER_STYLE: Record<string, { fill: number; cls: string }> = {
  A: { fill: 3, cls: "badge-accent" },
  B: { fill: 2, cls: "badge-brand" },
  C: { fill: 1, cls: "badge-neutral" },
};

/**
 * Signature element: a signal-strength meter instead of a flat colored
 * pill. Ties the "verified signal" brand concept directly to the one
 * thing this product actually does — everywhere a lead's tier appears.
 */
export function TierBadge({ tier }: { tier?: string | null }) {
  const t = (tier || "C").toUpperCase();
  const s = TIER_STYLE[t] || TIER_STYLE.C;
  return (
    <span className={`${s.cls} whitespace-nowrap`} title={`Tier ${t}`}>
      <span className="signal-meter" data-fill={s.fill} aria-hidden="true">
        <span />
        <span />
        <span />
      </span>
      Tier {t}
    </span>
  );
}

export function StagePill({ stage }: { stage?: string | null }) {
  if (!stage) return null;
  return <span className="badge-neutral whitespace-nowrap capitalize">{stage.replace(/_/g, " ")}</span>;
}

export function ScoreBar({ score }: { score?: number | null }) {
  const s = Math.max(0, Math.min(100, Number(score) || 0));
  const tone = s >= 75 ? "bg-brand-600 dark:bg-brand-300" : s >= 45 ? "bg-ink-faint" : "bg-amber-500";
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-ink-muted">
        <span>Score</span>
        <span className="font-semibold tabular-nums text-ink">{s}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-surface-3" role="presentation">
        <div className={`h-full rounded-full ${tone} transition-all`} style={{ width: `${s}%` }} />
      </div>
    </div>
  );
}
