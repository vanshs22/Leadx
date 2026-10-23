"use client";

/**
 * Notification list — shared markup for both consoles, so the client and
 * operator inboxes can't drift apart visually.
 */
import Link from "next/link";
import { useState } from "react";

export interface NotificationRow {
  id: string;
  kind: string;
  severity?: string;
  title: string;
  body?: string | null;
  link?: string | null;
  read_at?: string | null;
  created_at: string;
  tenant_name?: string;
}

const SEVERITY: Record<string, string> = {
  info: "bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-300",
  success: "bg-signal-50 text-signal-700 dark:bg-signal-500/15 dark:text-signal-300",
  warning: "bg-amber-50 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
  critical: "bg-rust-50 text-rust-700 dark:bg-rust-900/30 dark:text-rust-300",
};

export function NotificationList({
  rows,
  onMarkRead,
  onMarkAll,
  loading,
  error,
}: {
  rows: NotificationRow[];
  onMarkRead: (ids: string[]) => Promise<void> | void;
  onMarkAll: () => Promise<void> | void;
  loading?: boolean;
  error?: string;
}) {
  const [busy, setBusy] = useState(false);
  const unread = rows.filter((r) => !r.read_at);

  async function wrap(fn: () => Promise<void> | void) {
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-ink-muted">
          {unread.length} unread · {rows.length} total
        </p>
        <button aria-label="Action"
          type="button"
          disabled={busy || !unread.length}
          onClick={() => wrap(onMarkAll)}
          className="rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-2 disabled:opacity-40"
        >
          Mark all read
        </button>
      </div>

      {error && <p className="text-sm text-rust-500 dark:text-rust-300">{error}</p>}
      {loading && <p className="text-sm text-ink-faint">Loading…</p>}
      {!loading && !rows.length && (
        <p className="text-sm text-ink-faint">Nothing yet. Deliveries, replies and failures show up here.</p>
      )}

      <div className="space-y-2">
        {rows.map((n) => (
          <div
            key={n.id}
            className={`card-pad ${n.read_at ? "opacity-60" : "border-l-2 border-l-brand-500"}`}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`pill ${SEVERITY[n.severity || "info"] || SEVERITY.info}`}>
                    {n.severity || "info"}
                  </span>
                  <span className="font-mono text-[10px] uppercase tracking-wide text-ink-faint">
                    {n.kind}
                  </span>
                  {n.tenant_name ? (
                    <span className="text-[11px] text-ink-faint">· {n.tenant_name}</span>
                  ) : null}
                </div>
                <p className="mt-1 text-sm font-medium text-ink">{n.title}</p>
                {n.body ? <p className="mt-0.5 text-sm text-ink-muted">{n.body}</p> : null}
              </div>
              <div className="shrink-0 space-y-1 text-right">
                <div className="text-xs text-ink-faint">
                  {new Date(n.created_at).toLocaleString()}
                </div>
                {n.link ? (
                  <Link
                    href={n.link}
                    className="block text-xs font-medium text-brand-600 hover:underline dark:text-brand-300"
                  >
                    Open →
                  </Link>
                ) : null}
                {!n.read_at && (
                  <button aria-label="Action"
                    type="button"
                    disabled={busy}
                    onClick={() => wrap(() => onMarkRead([n.id]))}
                    className="text-xs text-ink-faint hover:text-ink disabled:opacity-40"
                  >
                    Mark read
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
