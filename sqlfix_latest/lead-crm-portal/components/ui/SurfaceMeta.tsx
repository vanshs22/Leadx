import type { ReactNode } from "react";

/** Small, explicit provenance/freshness cues for data-heavy surfaces. */
export function FreshnessNote({
  label = "CRM snapshot",
  detail,
}: {
  label?: string;
  detail?: string;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-ink-faint" title={detail || label}>
      <span className="h-1.5 w-1.5 rounded-full bg-signal-500" aria-hidden="true" />
      {label}
      {detail ? <span className="text-ink-faint">· {detail}</span> : null}
    </span>
  );
}

export function EvidenceNote({
  count,
  label = "evidence",
}: {
  count?: number | null;
  label?: string;
}) {
  if (typeof count !== "number") {
    return <span className="text-[11px] text-ink-faint">{label}: unavailable</span>;
  }
  return <span className="text-[11px] text-ink-faint">{count} {label}</span>;
}

export function UnavailableNote({
  label,
  children,
}: {
  label: string;
  children?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed border-line bg-surface-3/40 px-3 py-2 text-xs text-ink-muted">
      <span className="font-semibold text-ink">{label}: unavailable</span>
      {children ? <span className="ml-1">{children}</span> : null}
    </div>
  );
}
