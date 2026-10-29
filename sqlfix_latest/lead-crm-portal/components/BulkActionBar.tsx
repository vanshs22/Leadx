"use client";

/**
 * Floating "N selected" bar. Renders nothing at zero selection, so callers can
 * mount it unconditionally.
 */
export interface BulkAction {
  label: string;
  onClick: () => void;
  disabled?: boolean;
}

export function BulkActionBar({
  count,
  actions,
  onClear,
  noun = "selected",
}: {
  count: number;
  actions: BulkAction[];
  onClear: () => void;
  /** Trailing word after the count, e.g. "leads selected". */
  noun?: string;
}) {
  if (count <= 0) return null;
  return (
    <div
      role="region"
      aria-label="Bulk actions"
      className="fixed inset-x-3 bottom-4 z-40 mx-auto flex max-w-max flex-wrap items-center gap-2 rounded-xl border border-line-strong bg-surface-2 py-2 pl-4 pr-2 shadow-lift sm:bottom-6"
    >
      <span aria-live="polite" className="mr-1 flex items-center gap-2 text-[0.8125rem] font-semibold text-ink">
        <span className="inline-flex min-w-[1.5rem] items-center justify-center rounded-md bg-brand-700 px-1.5 py-0.5 text-xs tabular-nums text-white dark:bg-brand-300 dark:text-brand-900">
          {count}
        </span>
        <span className="font-medium text-ink-muted">{noun}</span>
      </span>
      <span className="hidden h-5 w-px bg-line sm:block" aria-hidden="true" />
      {actions.map((a, i) => (
        <button aria-label={a.label}
          key={a.label}
          type="button"
          className={`${i === 0 ? "btn-primary" : "btn-secondary"} btn-sm`}
          disabled={a.disabled}
          onClick={a.onClick}
        >
          {a.label}
        </button>
      ))}
      <button aria-label="Clear selected items" type="button" className="btn-ghost btn-sm" onClick={onClear}>
        Clear
      </button>
    </div>
  );
}
