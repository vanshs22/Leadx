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
      className="fixed bottom-6 left-1/2 z-40 flex -translate-x-1/2 flex-wrap items-center gap-3 rounded-2xl border border-line bg-surface-2 px-4 py-3 shadow-lift"
    >
      <span aria-live="polite" className="text-sm font-medium text-ink">
        {count} {noun}
      </span>
      {actions.map((a) => (
        <button aria-label="Action"
          key={a.label}
          type="button"
          className="btn-primary"
          disabled={a.disabled}
          onClick={a.onClick}
        >
          {a.label}
        </button>
      ))}
      <button aria-label="Action" type="button" className="btn-secondary" onClick={onClear}>
        Clear
      </button>
    </div>
  );
}
