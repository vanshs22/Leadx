"use client";

import { useLiveLoader } from "@/lib/live";
import { useCallback, useState } from "react";

import { NotificationList, type NotificationRow } from "@/components/layout/NotificationList";
import { PollingDeadlineState } from "@/components/ui/States";
import { PageHeader } from "@/components/ui/Primitives";
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
      <PageHeader eyebrow="Control plane" title="Notifications" description="Operator-scoped failed jobs, complaint spikes, and positive replies across every client." />

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
