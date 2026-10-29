"use client";

import { useCallback, useEffect, useState } from "react";

import { NotificationList, type NotificationRow } from "@/components/layout/NotificationList";
import { crmExtras } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { PageHeader, StatCard, StatGrid } from "@/components/ui/Primitives";

export default function NotificationsPage() {
  const [rows, setRows] = useState<NotificationRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const d = await crmExtras.notifications(getTenantId());
      setRows((d.notifications || []) as NotificationRow[]);
      setError("");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);
  useCrmChange(() => { void load(); }, { tables: ["notifications"] });

  const unread = rows.filter((row) => !row.read_at).length;
  const critical = rows.filter((row) => !row.read_at && (row.severity === "critical" || row.severity === "warning")).length;
  const fmt = (value: number) => (loading && !rows.length ? "—" : value.toLocaleString());

  return (
    <div className="page">
      <PageHeader
        eyebrow="Workspace"
        title="Notifications"
        description="Deliveries, replies, imports and sending problems"
        meta={<span className="inline-flex items-center gap-1.5"><span className="live-dot" aria-hidden="true" />Live inbox</span>}
      />

      <StatGrid cols={3}>
        <StatCard label="Unread" value={fmt(unread)} hint="Waiting for review" tone={unread ? "brand" : "neutral"} />
        <StatCard label="Needs attention" value={fmt(critical)} hint="Unread warnings and critical alerts" tone={critical ? "warn" : "neutral"} />
        <StatCard label="Total" value={fmt(rows.length)} hint="Most recent notifications" />
      </StatGrid>

      <NotificationList
        rows={rows}
        loading={loading}
        error={error}
        onMarkRead={async (ids) => {
          await crmExtras.markRead(getTenantId(), ids);
          await load();
        }}
        onMarkAll={async () => {
          await crmExtras.markAllRead(getTenantId());
          await load();
        }}
      />
    </div>
  );
}
