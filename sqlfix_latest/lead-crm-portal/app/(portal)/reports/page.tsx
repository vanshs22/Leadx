"use client";

import { useEffect, useState } from "react";
import { crm, ReportsSummary } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { FunnelChart } from "@/components/charts/FunnelChart";
import { EmptyState, ErrorState, LoadingRows, PollingDeadlineState } from "@/components/ui/States";
import { useLiveLoader } from "@/lib/live";
import { Badge, PageHeader, Panel, StatCard, StatGrid } from "@/components/ui/Primitives";
import { Icon } from "@/components/ui/Icon";
import { FreshnessNote } from "@/components/ui/SurfaceMeta";
import dynamic from "next/dynamic";
import Link from "next/link";

const TierDonut = dynamic(() => import("@/components/charts/TierDonut").then((m) => m.TierDonut), { ssr: false, loading: () => <div className="skeleton h-12 w-full rounded-lg" /> });

export default function ReportsPage() {
  const [data, setData] = useState<ReportsSummary | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [stale, setStale] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [deadline, setDeadline] = useState(false);

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

  useLiveLoader(() => { setRefreshKey((value) => value + 1); }, 30_000, [], { runImmediately: false, onDeadline: () => setDeadline(true) });

  useCrmChange(() => { setDeadline(false); setRefreshKey((value) => value + 1); }, { tables: ["lead_assignments", "leads_global", "lead_score_snapshots", "lead_quality_gates", "lead_verification_observations", "lead_outcome_events", "pipeline_stages"] });

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

  if (error && !data) return <div className="page"><PageHeader eyebrow="Measure" title="Reports" description="Quality mix and conversion snapshot." /><ErrorState error={error} onRetry={() => setRefreshKey((value) => value + 1)} /></div>;
  if (loading && !data) return <div className="page"><PageHeader eyebrow="Measure" title="Reports" description="Loading the latest report snapshot…" /><StatGrid cols={4}>{Array.from({ length: 4 }).map((_, i) => <div key={i} className="kpi"><div className="skeleton h-3 w-20" /><div className="skeleton mt-3 h-6 w-24" /></div>)}</StatGrid><div className="card"><LoadingRows rows={4} cols={4} /></div></div>;
  if (!data) return <div className="page"><PageHeader eyebrow="Measure" title="Reports" /><div className="card"><EmptyState title="Reports are unavailable" hint="No report snapshot was returned for this workspace." action={<button type="button" className="btn-secondary" onClick={() => setRefreshKey((value) => value + 1)}>Try again</button>} /></div></div>;

  const byTier = data.by_tier || {};
  const byStage = Object.entries(data.by_stage || {}).map(([stage, count]) => ({ stage, count: count as number }));
  const stageTotal = byStage.reduce((sum, s) => sum + (Number(s.count) || 0), 0);
  const tierRows = [
    { tier: "A", count: Number(byTier.A || 0), tone: "accent" as const },
    { tier: "B", count: Number(byTier.B || 0), tone: "brand" as const },
    { tier: "C", count: Number(byTier.C || 0), tone: "neutral" as const },
  ];
  const tierTotal = tierRows.reduce((sum, t) => sum + t.count, 0);
  const pct = (n: number, d: number) => (d ? `${Math.round((n / d) * 100)}%` : "—");
  const num = (v: unknown) => (typeof v === "number" ? v.toLocaleString() : String(v ?? "—"));

  return (
    <div className="page">
      <PageHeader eyebrow="Measure" title="Reports" description="Quality mix and conversion snapshot; cached data remains visible when the API is restarting." actions={<><button type="button" aria-label="Refresh reports" className="btn-secondary" onClick={() => { setDeadline(false); setRefreshKey((value) => value + 1); }} disabled={loading}><Icon name="refresh" size={14} aria-hidden="true" />{loading ? "Refreshing…" : "Refresh"}</button><button type="button" aria-label="Export report as CSV" onClick={exportCsv} disabled={exporting} className="btn-secondary"><Icon name="download" size={14} aria-hidden="true" />{exporting ? "Exporting…" : "Export CSV"}</button><Link href="/reports/deals" className="btn-ghost">Deal reporting</Link></>} meta={<FreshnessNote label={stale ? "Cached report" : "Fresh report"} detail={stale ? "refreshing in background" : "from CRM API"} />} />
      {deadline ? <PollingDeadlineState onRefresh={() => { setDeadline(false); setRefreshKey((value) => value + 1); }} /> : null}
      {error && data ? <ErrorState error={error} onRetry={() => setRefreshKey((value) => value + 1)} /> : null}

      <StatGrid cols={4}>
        <StatCard label="Total leads" value={num(data.total)} tone="brand" />
        <StatCard label="With email" value={num(data.with_email)} hint={<span className="tabular-nums">{data.with_email_pct}% of leads</span>} tone="success" />
        <StatCard label="With booking" value={num(data.with_booking)} />
        <StatCard label="Avg score" value={num(data.avg_score)} />
      </StatGrid>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Quality mix" description="Lead tiers in this workspace." bodyClassName="panel-body flex flex-col items-center gap-5">
          <TierDonut a={byTier.A || 0} b={byTier.B || 0} c={byTier.C || 0} />
          <table className="w-full text-[0.8125rem]">
            <caption className="sr-only">Leads by tier</caption>
            <thead><tr><th scope="col" className="label pb-2 text-left">Tier</th><th scope="col" className="label pb-2 text-right">Leads</th><th scope="col" className="label pb-2 text-right">Share</th></tr></thead>
            <tbody>{tierRows.map((t) => <tr key={t.tier} className="border-t border-line"><td className="py-2"><Badge tone={t.tone}>Tier {t.tier}</Badge></td><td className="py-2 text-right tabular-nums text-ink">{t.count.toLocaleString()}</td><td className="py-2 text-right tabular-nums text-ink-muted">{pct(t.count, tierTotal)}</td></tr>)}</tbody>
          </table>
        </Panel>
        <Panel title="By stage" description="Where leads sit in the lifecycle right now." className="lg:col-span-2">
          {byStage.length ? <FunnelChart stages={byStage} /> : <EmptyState title="No stage data yet" hint="Move leads through the lifecycle to populate this funnel." />}
        </Panel>
      </div>

      {byStage.length ? (
        <Panel title="Stage breakdown" description="Counts and share of all staged leads." bodyClassName="p-0">
          <div className="overflow-x-auto">
            <table className="data-table">
              <caption className="sr-only">Leads by stage</caption>
              <thead><tr><th scope="col">Stage</th><th scope="col" className="!text-right">Leads</th><th scope="col" className="!text-right">Share</th></tr></thead>
              <tbody>{byStage.map((s) => <tr key={s.stage}><td className="font-medium capitalize">{s.stage.replace(/_/g, " ")}</td><td className="text-right tabular-nums">{(Number(s.count) || 0).toLocaleString()}</td><td className="text-right tabular-nums text-ink-muted">{pct(Number(s.count) || 0, stageTotal)}</td></tr>)}</tbody>
            </table>
          </div>
        </Panel>
      ) : null}
    </div>
  );
}
