"use client";

export function QualityChips({
  tier,
  score,
  intent,
  opportunity,
  fit,
  contactability,
  sources,
  pitch,
  talkingPoints,
}: {
  tier?: string;
  score?: number | null;
  intent?: number | null;
  opportunity?: number | null;
  fit?: number | null;
  contactability?: number | null;
  sources?: string[] | string | null;
  pitch?: string | null;
  talkingPoints?: string | null;
}) {
  const src = Array.isArray(sources)
    ? sources
    : typeof sources === "string"
      ? sources.split(/[+,\s]+/).filter(Boolean)
      : [];

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {typeof score === "number" && (
          <span className="pill bg-brand-50 text-brand-700 ring-1 ring-brand-200 dark:bg-brand-500/15 dark:text-brand-300 dark:ring-brand-500/30">
            Score {score}
          </span>
        )}
        {typeof opportunity === "number" && (
          <span className="pill bg-signal-50 text-signal-700 ring-1 ring-signal-200 dark:bg-signal-500/15 dark:text-signal-300 dark:ring-signal-500/30">
            Opportunity {opportunity}
          </span>
        )}
        {typeof fit === "number" && <span className="pill bg-surface-3 text-ink-muted ring-1 ring-line">Fit {fit}</span>}
        {typeof intent === "number" && <span className="pill bg-fuchsia-50 text-fuchsia-700 ring-1 ring-fuchsia-200 dark:bg-fuchsia-500/15 dark:text-fuchsia-300 dark:ring-fuchsia-500/30">Intent {intent}</span>}
        {typeof contactability === "number" && <span className="pill bg-surface-3 text-ink-muted ring-1 ring-line">Reach {contactability}</span>}
        {src.slice(0, 5).map((s) => (
          <span key={s} className="pill bg-surface-3 text-ink-muted ring-1 ring-line">
            {s}
          </span>
        ))}
      </div>
      {(talkingPoints || pitch) && (
        <p className="text-sm leading-relaxed text-ink-muted">
          <span className="font-semibold text-ink">Say this: </span>
          {talkingPoints || pitch}
        </p>
      )}
    </div>
  );
}
