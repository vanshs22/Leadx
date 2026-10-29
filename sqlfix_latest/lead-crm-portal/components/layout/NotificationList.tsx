"use client";

/**
 * Notification list — shared markup for both consoles, so the client and
 * operator inboxes can't drift apart visually.
 */
import Link from "next/link";
import { useState } from "react";
import { Badge, Panel, type Tone } from "@/components/ui/Primitives";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/States";
import { Icon } from "@/components/ui/Icon";

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

const SEVERITY: Record<string, Tone> = {
  info: "brand",
  success: "success",
  warning: "warn",
  critical: "danger",
};

const SEVERITY_RAIL: Record<string, string> = {
  info: "bg-brand-500 dark:bg-brand-300",
  success: "bg-emerald-600 dark:bg-emerald-400",
  warning: "bg-amber-500",
  critical: "bg-rust-500",
};

function formatWhen(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

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
    <Panel
      id="notification-inbox"
      title="Inbox"
      description={
        <span className="tabular-nums">
          {unread.length} unread · {rows.length} total
        </span>
      }
      actions={
        <button
          type="button"
          aria-label="Mark all notifications read"
          disabled={busy || !unread.length}
          onClick={() => wrap(onMarkAll)}
          className="btn-secondary btn-sm"
        >
          <Icon name="check" size={14} />
          Mark all read
        </button>
      }
      bodyClassName="p-0"
    >
      {error ? (
        <div className="p-4">
          <ErrorState error={error} />
        </div>
      ) : null}
      {loading && !rows.length ? <LoadingRows rows={5} cols={3} /> : null}
      {!loading && !error && !rows.length ? (
        <EmptyState
          title="No notifications yet"
          hint="Deliveries, replies and failures show up here."
          icon={<Icon name="bell" size={18} />}
        />
      ) : null}

      {rows.length ? (
        <ul className="divide-y divide-line" aria-label="Notifications" aria-busy={busy || undefined}>
          {rows.map((n) => {
            const severity = n.severity || "info";
            const isUnread = !n.read_at;
            return (
              <li
                key={n.id}
                className={`relative flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-start sm:justify-between ${isUnread ? "" : "bg-surface-3/30"}`}
              >
                {isUnread ? (
                  <span className={`absolute inset-y-3 left-0 w-[3px] rounded-r ${SEVERITY_RAIL[severity] || SEVERITY_RAIL.info}`} aria-hidden="true" />
                ) : null}
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={SEVERITY[severity] || "brand"} className="capitalize">{severity}</Badge>
                    <span className="font-mono text-xs text-ink-faint">{n.kind}</span>
                    {n.tenant_name ? <span className="text-xs text-ink-faint">· {n.tenant_name}</span> : null}
                    {isUnread ? <span className="sr-only">Unread</span> : null}
                  </div>
                  <p className={`mt-1.5 text-sm ${isUnread ? "font-semibold text-ink" : "font-medium text-ink-muted"}`}>{n.title}</p>
                  {n.body ? <p className="mt-0.5 text-[0.8125rem] leading-5 text-ink-muted">{n.body}</p> : null}
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-2 sm:flex-col sm:items-end sm:gap-1.5">
                  <time dateTime={n.created_at} className="text-xs tabular-nums text-ink-faint">
                    {formatWhen(n.created_at)}
                  </time>
                  <div className="flex items-center gap-1">
                    {n.link ? (
                      <Link href={n.link} className="btn-ghost btn-sm" aria-label={`Open ${n.title}`}>
                        Open
                        <Icon name="arrow" size={14} />
                      </Link>
                    ) : null}
                    {isUnread && (
                      <button
                        type="button"
                        aria-label={`Mark ${n.title} read`}
                        disabled={busy}
                        onClick={() => wrap(() => onMarkRead([n.id]))}
                        className="btn-ghost btn-sm"
                      >
                        Mark read
                      </button>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      ) : null}
    </Panel>
  );
}
