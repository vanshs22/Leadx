"use client";

import { useCallback, useEffect, useState } from "react";

import { NotificationList, type NotificationRow } from "@/components/layout/NotificationList";
import { crmExtras } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";

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

  return (
    <div className="page">
      <header>
        <h1 className="page-title">Notifications</h1>
        <p className="page-sub">Deliveries, replies, imports and sending problems</p>
      </header>

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
