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
    <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-ink-faint" title={detail || label}>
      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand-500 dark:bg-brand-300" aria-hidden="true" />
      {label}
      {detail ? <span className="font-normal tabular-nums text-ink-faint">· {detail}</span> : null}
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
  return <span className="text-[11px] text-ink-faint"><span className="font-semibold tabular-nums text-ink-muted">{count}</span> {label}</span>;
}

export function UnavailableNote({
  label,
  children,
}: {
  label: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-1 rounded-lg border border-dashed border-line-strong bg-surface-3/50 px-3 py-2 text-xs text-ink-muted">
      <span className="font-semibold text-ink">{label}: unavailable</span>
      {children ? <span className="ml-1">{children}</span> : null}
    </div>
  );
}
