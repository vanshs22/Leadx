"use client";

const TIER_STYLE: Record<string, { fill: number; text: string; bg: string }> = {
  A: { fill: 3, text: "text-brand-700 dark:text-brand-300", bg: "bg-brand-50 dark:bg-brand-500/15" },
  B: { fill: 2, text: "text-signal-700 dark:text-signal-300", bg: "bg-signal-50 dark:bg-signal-500/15" },
  C: { fill: 1, text: "text-ink-faint", bg: "bg-surface-3" },
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
    <span className={`pill ${s.bg} ${s.text}`}>
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
  return (
    <span className="pill border border-line bg-surface-3 text-ink-muted">
      {stage}
    </span>
  );
}

export function ScoreBar({ score }: { score?: number | null }) {
  const s = Math.max(0, Math.min(100, Number(score) || 0));
  const tone = s >= 75 ? "bg-brand-500" : s >= 45 ? "bg-signal-500" : "bg-rust-400";
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-ink-muted">
        <span>Score</span>
        <span className="font-mono font-medium tabular-nums text-ink">{s}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-surface-3">
        <div className={`h-full rounded-full ${tone} transition-all`} style={{ width: `${s}%` }} />
      </div>
    </div>
  );
}
