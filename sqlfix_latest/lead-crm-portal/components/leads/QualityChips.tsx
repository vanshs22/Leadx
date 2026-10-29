"use client";

/**
 * Compact quality readout for a lead: score, opportunity, fit, intent, reach
 * and provenance. Neutral by default; emerald for strong values, amber for
 * weak ones, so the eye lands on what is notable rather than on every chip.
 */
function toneFor(value: number): string {
  if (value >= 75) return "badge-success";
  if (value < 40) return "badge-warn";
  return "badge-neutral";
}

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

  const metrics: Array<{ label: string; value: number | null | undefined }> = [
    { label: "Score", value: score },
    { label: "Opportunity", value: opportunity },
    { label: "Fit", value: fit },
    { label: "Intent", value: intent },
    { label: "Reach", value: contactability },
  ];

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {metrics.map((m) =>
          typeof m.value === "number" ? (
            <span key={m.label} className={toneFor(m.value)} title={`${m.label} ${m.value} of 100`}>
              <span className="font-medium opacity-80">{m.label}</span>
              <span className="tabular-nums">{m.value}</span>
            </span>
          ) : null,
        )}
        {src.slice(0, 5).map((s) => (
          <span key={s} className="badge-neutral font-medium" title={`Source: ${s}`}>
            {s}
          </span>
        ))}
      </div>
      {(talkingPoints || pitch) && (
        <p className="text-[0.8125rem] leading-5 text-ink-muted">
          <span className="font-semibold text-ink">Say this: </span>
          {talkingPoints || pitch}
        </p>
      )}
    </div>
  );
}
