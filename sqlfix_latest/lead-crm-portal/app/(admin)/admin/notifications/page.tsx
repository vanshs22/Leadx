"use client";

import { useLiveLoader } from "@/lib/live";
import { useCallback, useState } from "react";

import { NotificationList, type NotificationRow } from "@/components/layout/NotificationList";
import { PollingDeadlineState } from "@/components/ui/States";
import { adminExtras } from "@/lib/admin";

export default function AdminNotificationsPage() {
  const [rows, setRows] = useState<NotificationRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [pollingExpired, setPollingExpired] = useState(false);
  const [pollingReset, setPollingReset] = useState(0);

  const load = useCallback(async () => {
    try {
      const d = await adminExtras.notifications();
      setRows((d.notifications || []) as NotificationRow[]);
      setError("");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const refresh = () => {
    setPollingExpired(false);
    setPollingReset((value) => value + 1);
    void load();
  };

  useLiveLoader(load, 30_000, [pollingReset], {
    onDeadline: () => setPollingExpired(true),
  });

  return (
    <div className="page">
      <header>
        <h1 className="page-title">Notifications</h1>
        <p className="page-sub">
          Operator-scoped: failed jobs, complaint spikes, positive replies across every client
        </p>
      </header>

      {pollingExpired && <PollingDeadlineState onRefresh={refresh} />}

      <NotificationList
        rows={rows}
        loading={loading}
        error={error}
        onMarkRead={async (ids) => {
          await adminExtras.markRead(ids);
          await load();
        }}
        onMarkAll={async () => {
          await adminExtras.markAllRead();
          await load();
        }}
      />
    </div>
  );
}
