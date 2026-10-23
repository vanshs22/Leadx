"use client";

import { useLiveLoader } from "@/lib/live";
import { useState } from "react";
import { admin } from "@/lib/admin";
import { ErrorState, PollingDeadlineState } from "@/components/ui/States";

export default function AdminUsagePage() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [msg, setMsg] = useState("");

  const [pollingExpired, setPollingExpired] = useState(false);
  const [pollingReset, setPollingReset] = useState(0);

  async function load() {
    setLoading(true);
    setLoadError(null);
    try {
      setData(await admin.usage());
    } catch (caught) {
      setLoadError(caught);
    } finally {
      setLoading(false);
    }
  }

  const refresh = () => {
    setPollingExpired(false);
    setPollingReset((value) => value + 1);
    void load();
  };

  useLiveLoader(load, 30_000, [pollingReset], {
    onDeadline: () => setPollingExpired(true),
  });

  async function reportAll() {
    setMsg("Reporting all to Stripe…");
    try {
      const r = await admin.billingReportAll();
      setMsg(`Reported ${r.reported ?? "all"} tenants to Stripe`);
      refresh();
    } catch (e: any) {
      setMsg(e.message);
    }
  }

  async function reportOne(tenantId: string) {
    setBusyId(tenantId);
    try {
      await admin.billingReport(tenantId);
      refresh();
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBusyId(null);
    }
  }

  const rows = data?.usage || [];
  const totalDelivered = rows.reduce((s: number, r: any) => s + (r.leads_delivered || 0), 0);
  const totalCredited = rows.reduce((s: number, r: any) => s + (r.leads_credited || 0), 0);
  const unreported = rows.filter((r: any) => !r.stripe_reported_at).length;

  return (
    <div className="page">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="page-title">Usage & billing</h1>
          <p className="page-sub">Period: {data?.period || "…"} · net delivered leads</p>
        </div>
        <button aria-label="Action" onClick={reportAll} className="btn-primary">
          Report all to Stripe
        </button>
      </header>

      {pollingExpired && <PollingDeadlineState onRefresh={refresh} />}
      {loadError ? <ErrorState error={loadError} onRetry={refresh} /> : null}
      {msg && <p className="text-sm text-ink-muted">{msg}</p>}

      <div className="grid gap-4 sm:grid-cols-3">
        <div className="kpi">
          <div className="label">Leads delivered</div>
          <div className="kpi-value mt-2">{loading ? "…" : totalDelivered}</div>
        </div>
        <div className="kpi">
          <div className="label">Leads credited</div>
          <div className="kpi-value mt-2 text-rust-500 dark:text-rust-300">{loading ? "…" : totalCredited}</div>
        </div>
        <div className="kpi">
          <div className="label">Not yet reported</div>
          <div className="kpi-value mt-2">{loading ? "…" : unreported}</div>
        </div>
      </div>

      <div className="card overflow-hidden">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-line bg-surface-3/60 text-xs uppercase tracking-wide text-ink-faint">
            <tr>
              <th className="px-4 py-3 font-medium">Tenant</th>
              <th className="px-4 py-3 font-medium">Plan</th>
              <th className="px-4 py-3 font-medium">Delivered</th>
              <th className="px-4 py-3 font-medium">Credited</th>
              <th className="px-4 py-3 font-medium">Stripe qty</th>
              <th className="px-4 py-3 font-medium">Reported at</th>
              <th className="px-4 py-3 font-medium" />
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((u: any) => (
              <tr key={u.tenant_id} className="hover:bg-surface-3/40">
                <td className="px-4 py-3 font-medium text-ink">
                  {u.tenant_name || `${u.tenant_id?.slice(0, 8)}…`}
                </td>
                <td className="px-4 py-3 text-ink-muted">{u.plan || "—"}</td>
                <td className="px-4 py-3 font-mono tabular-nums text-ink">{u.leads_delivered || 0}</td>
                <td className="px-4 py-3 font-mono tabular-nums text-ink-faint">{u.leads_credited || 0}</td>
                <td className="px-4 py-3 font-mono tabular-nums text-ink-faint">{u.stripe_reported_qty ?? "—"}</td>
                <td className="px-4 py-3 text-xs text-ink-faint">
                  {u.stripe_reported_at ? new Date(u.stripe_reported_at).toLocaleString() : "—"}
                </td>
                <td className="px-4 py-3 text-right">
                  <button aria-label="Action"
                    onClick={() => reportOne(u.tenant_id)}
                    disabled={busyId === u.tenant_id}
                    className="btn-ghost text-xs disabled:opacity-50"
                  >
                    {busyId === u.tenant_id ? "…" : "Report"}
                  </button>
                </td>
              </tr>
            ))}
            {!loading && rows.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center text-ink-faint">
                  No usage this period
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
