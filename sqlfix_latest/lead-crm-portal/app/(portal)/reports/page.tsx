"use client";

import { useEffect, useState } from "react";
import { crm, ReportsSummary } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { FunnelChart } from "@/components/charts/FunnelChart";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/States";
import { FreshnessNote } from "@/components/ui/SurfaceMeta";
import dynamic from "next/dynamic";

const TierDonut = dynamic(() => import("@/components/charts/TierDonut").then((m) => m.TierDonut), { ssr: false, loading: () => <div className="skeleton h-12 w-full rounded-lg" /> });

export default function ReportsPage() {
  const [data, setData] = useState<ReportsSummary | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [stale, setStale] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const tid = getTenantId();
    if (!tid) {
      setError(new Error("No workspace selected"));
      setLoading(false);
      return () => { cancelled = true; };
    }
    const key = `leadx:reports:${tid}`;
    let hasCached = false;
    try {
      const cached = sessionStorage.getItem(key);
      if (cached) {
        const parsed = JSON.parse(cached);
        if (parsed?.total != null) {
          setData(parsed);
          setStale(true);
          hasCached = true;
        }
      }
    } catch {}
    crm.reports(tid)
      .then((next) => {
        if (cancelled) return;
        setData(next);
        setStale(false);
        setError(null);
        try { sessionStorage.setItem(key, JSON.stringify(next)); } catch {}
      })
      .catch((caught) => {
        if (cancelled) return;
        setError(caught);
        if (!hasCached) setData(null);
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [refreshKey]);

  async function exportCsv() {
    setExporting(true);
    try {
      const res = await crm.exportCsv(getTenantId());
      if (!res.rows?.length) {
        setError(new Error("No leads to export yet"));
        return;
      }
      const headers = Object.keys(res.rows[0]);
      const csv = [headers.join(","), ...res.rows.map((row: any) => headers.map((h) => JSON.stringify(row[h] ?? "")).join(","))].join("\n");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
      a.download = res.filename || "leads.csv";
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (caught) {
      setError(caught);
    } finally {
      setExporting(false);
    }
  }

  if (error && !data) return <div className="page"><ErrorState error={error} onRetry={() => setRefreshKey((value) => value + 1)} /></div>;
  if (loading && !data) return <div className="page"><h1 className="page-title">Reports</h1><LoadingRows rows={4} cols={4} /></div>;
  if (!data) return <div className="page"><EmptyState title="Reports are unavailable" hint="No report snapshot was returned for this workspace." action={<button type="button" className="btn-secondary" onClick={() => setRefreshKey((value) => value + 1)}>Try again</button>} /></div>;

  const byTier = data.by_tier || {};
  const byStage = Object.entries(data.by_stage || {}).map(([stage, count]) => ({ stage, count: count as number }));

  return (
    <div className="page">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="page-title">Reports</h1>
          <p className="page-sub">Quality mix and conversion snapshot · cached data remains visible when the API is restarting.</p>
          <div className="mt-2 flex flex-wrap items-center gap-3"><FreshnessNote label={stale ? "Cached report" : "Fresh report"} detail={stale ? "refreshing in background" : "from CRM API"} />{error ? <span className="text-[11px] text-rust-500 dark:text-rust-300">Refresh issue: {error instanceof Error ? error.message : "report request failed"}</span> : null}</div>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" className="btn-secondary" onClick={() => setRefreshKey((value) => value + 1)} disabled={loading}>{loading ? "Refreshing…" : "Refresh"}</button>
          <button aria-label="Action" onClick={exportCsv} disabled={exporting} className="btn-secondary disabled:opacity-50">{exporting ? "Exporting…" : "Export CSV"}</button>
        </div>
      </header>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[{ label: "Total leads", value: data.total }, { label: "With email", value: `${data.with_email} (${data.with_email_pct}%)` }, { label: "With booking", value: data.with_booking }, { label: "Avg score", value: data.avg_score }].map((c) => (
          <div key={c.label} className="kpi"><div className="label">{c.label}</div><div className="kpi-value mt-2">{c.value}</div></div>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="card-pad flex flex-col items-center justify-center gap-4"><h2 className="self-start text-sm font-semibold text-ink">Quality mix</h2><TierDonut a={byTier.A || 0} b={byTier.B || 0} c={byTier.C || 0} /></div>
        <div className="card-pad lg:col-span-2"><h2 className="text-sm font-semibold text-ink">By stage</h2><div className="mt-4">{byStage.length ? <FunnelChart stages={byStage} /> : <EmptyState title="No stage data yet" hint="Move leads through the lifecycle to populate this funnel." />}</div></div>
      </div>
    </div>
  );
}
