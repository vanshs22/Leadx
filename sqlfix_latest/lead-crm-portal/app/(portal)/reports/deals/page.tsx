"use client";

import { useCallback, useEffect, useState } from "react";
import { crmExtras, type DealReport } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { useLiveLoader } from "@/lib/live";
import { PageHeader, Notice, Panel, StatCard, StatGrid, Tabs } from "@/components/ui/Primitives";
import { DataState, EmptyState, LoadingRows } from "@/components/ui/States";

const WINDOWS = [{ days: 90, label: "90 days" }, { days: 180, label: "6 months" }, { days: 365, label: "12 months" }];
export default function DealReportsPage() {
  const [data, setData] = useState<DealReport | null>(null), [days, setDays] = useState(365), [error, setError] = useState<unknown>(null), [loading, setLoading] = useState(true), [pollingExpired, setPollingExpired] = useState(false), [pollingGeneration, setPollingGeneration] = useState(0);
  const load = useCallback(async () => { setLoading(true); setError(null); try { setData(await crmExtras.dealReport(getTenantId(), days)); } catch (caught) { setError(caught); } finally { setLoading(false); } }, [days]);
  useEffect(() => { void load(); }, [load]);
  useLiveLoader(() => load(), 30_000, [load, pollingGeneration], { runImmediately: false, onDeadline: () => setPollingExpired(true) });
  useCrmChange(() => { void load(); }, { tables: ["deals", "deal_activities", "lead_assignments"] });
  const refresh = () => { setPollingExpired(false); setPollingGeneration((value) => value + 1); void load(); };
  const cur = data?.currency || "USD", money = (v: number) => new Intl.NumberFormat(undefined, { style: "currency", currency: cur, maximumFractionDigits: 0 }).format(v || 0), maxMonth = Math.max(1, ...(data?.monthly_revenue || []).map((m) => m.value)), maxStage = Math.max(1, ...(data?.by_stage || []).map((s) => s.value));
  const windowLabel = WINDOWS.find((w) => w.days === days)?.label || `${days} days`;
  return (
    <div className="page">
      <PageHeader
        eyebrow="Measure"
        title="Deal reporting"
        description="Revenue, forecast and lead attribution."
        actions={<Tabs label="Reporting window" value={String(days)} onChange={(value) => setDays(Number(value))} items={WINDOWS.map((w) => ({ value: String(w.days), label: w.label }))} />}
        meta={<span>Window: <span className="tabular-nums">{windowLabel}</span> · currency <span className="font-mono">{cur}</span></span>}
      />
      {pollingExpired ? <Notice tone="warning" title="Live deal updates paused">Refresh to start a new bounded live-update window.</Notice> : null}
      {error ? <Notice tone="danger" title="Report unavailable">{error instanceof Error ? error.message : "Could not load deal reporting."}</Notice> : null}
      <DataState
        loading={loading}
        error={error}
        onRetry={refresh}
        isEmpty={!!data && !data.ok}
        empty={<div className="card"><EmptyState title="No deal data yet" hint="Convert a lead into a deal from the Leads page to start tracking." /></div>}
        skeleton={<div className="space-y-4"><StatGrid cols={4}>{Array.from({ length: 4 }).map((_, i) => <div key={i} className="kpi"><div className="skeleton h-3 w-24" /><div className="skeleton mt-3 h-6 w-28" /><div className="skeleton mt-3 h-3 w-20" /></div>)}</StatGrid><div className="card"><LoadingRows rows={5} cols={4} /></div></div>}
      >
        <>{data?.ok ? <>
          <StatGrid cols={4}>
            <StatCard label="Revenue won" value={money(data.revenue_won)} hint={<span className="tabular-nums">{data.deals_won} deals</span>} tone="success" />
            <StatCard label="Weighted forecast" value={money(data.forecast_weighted)} hint={<span className="tabular-nums">{data.open_deals} open · {money(data.open_value)} gross</span>} tone="brand" />
            <StatCard label="Win rate" value={`${data.win_rate}%`} hint={<span className="tabular-nums">avg {money(data.avg_deal_value)}</span>} />
            <StatCard label="From delivered leads" value={money(data.revenue_from_delivered_leads)} hint={<span className="tabular-nums">{data.attribution_pct}% of won revenue</span>} tone="accent" />
          </StatGrid>
          {data.avg_cycle_days != null ? <p className="text-xs text-ink-faint">Average time from created to won: <span className="tabular-nums">{data.avg_cycle_days}</span> days.</p> : null}
          <div className="grid gap-4 lg:grid-cols-5">
            <Panel title="Revenue by month" description={`Closed-won revenue, ${windowLabel.toLowerCase()}.`} className="lg:col-span-3">
              {data.monthly_revenue.length === 0 ? <EmptyState title="No closed-won deals in this window yet" /> : (
                <div className="flex h-48 items-end gap-1.5 border-b border-line pb-0" role="img" aria-label="Bar chart of closed-won revenue by month">
                  {data.monthly_revenue.map((m) => (
                    <div key={m.month} className="flex h-full flex-1 flex-col items-center justify-end gap-1.5">
                      <div title={`${m.month}: ${money(m.value)}`} className="w-full max-w-[40px] rounded-t-sm bg-brand-600 dark:bg-brand-300" style={{ height: `${Math.max(2, Math.round((m.value / maxMonth) * 100))}%` }} />
                      <span className="text-[10px] tabular-nums text-ink-faint">{m.month.slice(2)}</span>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
            <Panel title="Open pipeline by stage" description="Gross value relative to the largest stage." className="lg:col-span-2">
              {data.by_stage.length === 0 ? <EmptyState title="No open deals" /> : (
                <div className="space-y-3">
                  {data.by_stage.map((s) => (
                    <div key={s.stage} className="space-y-1.5">
                      <div className="flex justify-between gap-3 text-xs"><span className="font-medium capitalize text-ink">{s.stage}</span><span className="tabular-nums text-ink-muted">{money(s.value)}</span></div>
                      <div className="h-1.5 overflow-hidden rounded-full bg-surface-3"><div className="h-full rounded-full bg-brand-600 dark:bg-brand-300" style={{ width: `${Math.round((s.value / maxStage) * 100)}%` }} /></div>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          </div>
          {data.by_stage.length ? (
            <Panel title="Stage detail" description="Count, gross and probability-weighted value per open stage." bodyClassName="p-0">
              <div className="overflow-x-auto">
                <table className="data-table">
                  <caption className="sr-only">Open pipeline by stage</caption>
                  <thead><tr><th scope="col">Stage</th><th scope="col" className="!text-right">Deals</th><th scope="col" className="!text-right">Gross value</th><th scope="col" className="!text-right">Weighted</th></tr></thead>
                  <tbody>{data.by_stage.map((s) => <tr key={s.stage}><td className="font-medium capitalize">{s.stage}</td><td className="text-right tabular-nums">{s.count}</td><td className="text-right tabular-nums">{money(s.value)}</td><td className="text-right tabular-nums text-ink-muted">{money(s.weighted)}</td></tr>)}</tbody>
                </table>
              </div>
            </Panel>
          ) : null}
        </> : null}</>
      </DataState>
    </div>
  );
}
