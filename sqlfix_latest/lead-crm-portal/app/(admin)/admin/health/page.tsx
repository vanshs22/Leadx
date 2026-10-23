"use client";

import { useLiveLoader } from "@/lib/live";
import { useState } from "react";
import Link from "next/link";
import { admin } from "@/lib/admin";
import { ErrorState, PollingDeadlineState } from "@/components/ui/States";
import { PageHeader } from "@/components/ui/Primitives";

const RISK_STYLES: Record<string, string> = {
  churn_risk: "bg-rust-50 text-rust-700 dark:bg-rust-500/15 dark:text-rust-300",
  never_active: "bg-brand-50 text-brand-800 dark:bg-brand-500/15 dark:text-brand-700 dark:text-brand-300",
  stale: "bg-orange-50 text-orange-700 dark:bg-orange-500/15 dark:text-orange-300",
  quality_issues: "bg-purple-50 text-purple-700 dark:bg-purple-500/15 dark:text-purple-300",
  ok: "bg-signal-50 text-signal-700 dark:bg-signal-500/15 dark:text-signal-300",
};

export default function AdminTenantHealthPage() {
  const [data, setData] = useState<{ tenants: any[]; summary: any } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [pollingExpired, setPollingExpired] = useState(false);
  const [pollingReset, setPollingReset] = useState(0);
  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await admin.tenantHealth());
    } catch (caught) {
      setError(caught);
    } finally {
      setLoading(false);
    }
  };

  const refresh = () => {
    setPollingExpired(false);
    setPollingReset((value) => value + 1);
    void load();
  };

  useLiveLoader(load, 30_000, [pollingReset], {
    onDeadline: () => setPollingExpired(true),
  });

  if (loading) return <div className="page text-ink-faint" aria-busy="true">Loading tenant health…</div>;
  if (error) return <div className="page"><ErrorState error={error} onRetry={refresh} /></div>;

  const s = data?.summary || {};
  const tenants = data?.tenants || [];

  return (
    <div className="page">
      <PageHeader eyebrow="Control plane" title="Tenant health" description="Churn risk, usage, and quality signals — catch problems before the client cancels." meta={<span className="text-xs text-ink-faint">Authoritative tenant health snapshot</span>} />

      {pollingExpired && <PollingDeadlineState onRefresh={refresh} />}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {[
          { label: "Total", value: s.total, tone: "text-ink" },
          { label: "Churn risk", value: s.churn_risk, tone: "text-rust-500" },
          { label: "Never active", value: s.never_active, tone: "text-brand-600" },
          { label: "Stale", value: s.stale, tone: "text-orange-600" },
          { label: "Healthy", value: s.ok, tone: "text-signal-600" },
        ].map((c) => (
          <div key={c.label} className="card-pad">
            <div className="text-xs text-ink-faint">{c.label}</div>
            <div className={`mt-1 text-2xl font-semibold ${c.tone}`}>{c.value ?? "—"}</div>
          </div>
        ))}
      </div>

      <div className="overflow-x-auto rounded-xl border border-line">
        <table className="w-full text-left text-sm">
          <thead className="bg-surface-2 text-xs uppercase tracking-wide text-ink-faint">
            <tr>
              <th className="px-4 py-3">Client</th>
              <th className="px-4 py-3">Plan</th>
              <th className="px-4 py-3">Risk</th>
              <th className="px-4 py-3">Last activity</th>
              <th className="px-4 py-3">Deliveries (30d)</th>
              <th className="px-4 py-3">Runs (30d)</th>
              <th className="px-4 py-3">Open bad</th>
            </tr>
          </thead>
          <tbody>
            {tenants.map((t: any) => (
              <tr key={t.tenant_id} className="border-t border-line">
                <td className="px-4 py-3">
                  <Link
                    href={`/admin/tenants/${t.tenant_id}`}
                    className="font-medium text-brand-600 hover:underline dark:text-brand-700 dark:text-brand-300"
                  >
                    {t.name || t.tenant_id.slice(0, 8)}
                  </Link>
                </td>
                <td className="px-4 py-3 text-ink-muted">{t.plan || "—"}</td>
                <td className="px-4 py-3">
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                      RISK_STYLES[t.risk] || RISK_STYLES.ok
                    }`}
                  >
                    {t.risk?.replace("_", " ")}
                  </span>
                </td>
                <td className="px-4 py-3 text-ink-muted">
                  {t.last_activity
                    ? new Date(t.last_activity).toLocaleDateString()
                    : "Never"}
                </td>
                <td className="px-4 py-3">{t.deliveries_30d}</td>
                <td className="px-4 py-3">{t.runs_30d}</td>
                <td className="px-4 py-3">
                  {t.open_bad_reports > 0 ? (
                    <span className="font-medium text-rust-500">{t.open_bad_reports}</span>
                  ) : (
                    "0"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
