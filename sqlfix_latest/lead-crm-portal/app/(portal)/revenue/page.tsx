"use client";

import { useLiveLoader } from "@/lib/live";
import { EmptyState, ErrorState, LoadingRows, PollingDeadlineState } from "@/components/ui/States";
import { useState, type ReactNode } from "react";
import Link from "next/link";
import { Badge, PageHeader, Panel, StatCard, StatGrid } from "@/components/ui/Primitives";
import { Icon } from "@/components/ui/Icon";
import { crmIntelligence } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";

const labels: Record<string, string> = { discovered_or_assigned: "Assigned", contacted: "Contacted", replied: "Replied", positive_replies: "Positive", meetings: "Meetings", opportunities: "Opportunities", won: "Won" };

export default function RevenuePage() {
  const [data, setData] = useState<any>(null);
  const [learning, setLearning] = useState<any>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [deadlineReached, setDeadlineReached] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  async function load() {
    const tenantId = getTenantId();
    if (!tenantId) {
      setError("Choose a workspace to view revenue intelligence.");
      setLoading(false);
      return;
    }
    try {
      const [funnel, learningResult] = await Promise.all([
        crmIntelligence.revenueFunnel(tenantId, 90),
        crmIntelligence.learning(tenantId),
      ]);
      setData(funnel);
      setLearning(learningResult);
      setError(null);
    } catch (caught: unknown) {
      setError(caught);
    } finally {
      setLoading(false);
    }
  }

  const refresh = () => {
    setDeadlineReached(false);
    setLoading(true);
    setRefreshKey((value) => value + 1);
  };

  useLiveLoader(load, 30_000, [refreshKey], { onDeadline: () => setDeadlineReached(true) });

  const header = (actions?: ReactNode) => <PageHeader eyebrow="Revenue intelligence" title="From signal to revenue" description="A single view of what LeadX discovers, contacts, converts and ultimately helps close." actions={actions} meta={<span>Window: <span className="tabular-nums">last 90 days</span></span>} />;

  if (loading && !data) return <div className="page">{header()}<StatGrid cols={4}>{Array.from({ length: 4 }).map((_, i) => <div key={i} className="kpi"><div className="skeleton h-3 w-20" /><div className="skeleton mt-3 h-6 w-24" /></div>)}</StatGrid><div className="card"><LoadingRows rows={4} cols={3} /></div></div>;
  if (error && !data) return <div className="page">{header()}<ErrorState error={error} onRetry={refresh} /></div>;

  const funnel = data?.funnel || {};
  const hasData = Object.keys(funnel).length > 0 || Boolean(learning);
  const usd = (v: unknown) => `$${Number(v || 0).toLocaleString()}`;
  return <div className="page">
    {header(<><button type="button" aria-label="Refresh revenue intelligence" className="btn-secondary" onClick={refresh} disabled={loading}><Icon name="refresh" size={14} aria-hidden="true" />{loading ? "Refreshing…" : "Refresh"}</button><Link href="/reports" className="btn-secondary">Reports<Icon name="arrow" size={14} aria-hidden="true" /></Link></>)}
    {deadlineReached ? <PollingDeadlineState onRefresh={refresh} /> : null}
    {error ? <ErrorState error={error} onRetry={refresh} /> : null}
    {!hasData ? <div className="card"><EmptyState title="No revenue outcomes yet" hint="Move a lead through outreach or pipeline stages to start the revenue feedback loop." action={<Link href="/pipeline" className="btn-secondary">Open pipeline</Link>} /></div> : null}
    {hasData ? <>
      <StatGrid cols={4}>
        <StatCard label="Revenue won" value={usd(data?.revenue_won)} tone="success" />
        <StatCard label="Reply rate" value={`${data?.reply_rate_pct ?? 0}%`} tone="brand" />
        <StatCard label="Positive rate" value={`${data?.positive_rate_pct ?? 0}%`} />
        <StatCard label="Avg won value" value={usd(data?.avg_won_value)} />
      </StatGrid>
      <Panel title="Where opportunities move forward" description="Conversion chain from assignment to won." actions={<Badge tone="neutral">Last 90 days</Badge>} bodyClassName="p-0">
        <div className="overflow-x-auto">
          <table className="data-table">
            <caption className="sr-only">Revenue conversion chain</caption>
            <thead><tr><th scope="col">Stage</th><th scope="col" className="!text-right">Count</th><th scope="col" className="!text-right">Conversion</th><th scope="col" className="w-[40%]"><span className="sr-only">Conversion bar</span></th></tr></thead>
            <tbody>{Object.entries(funnel).map(([key, value]: [string, any]) => { const pct = data?.conversion_pct?.[key] ?? 0; return <tr key={key}><td className="font-medium">{labels[key] || key}</td><td className="text-right tabular-nums">{Number(value || 0).toLocaleString()}</td><td className="text-right tabular-nums text-ink-muted">{pct}%</td><td><div className="h-1.5 rounded-full bg-surface-3" aria-hidden="true"><div className="h-full rounded-full bg-brand-600 dark:bg-brand-300" style={{ width: `${Math.min(100, Math.max(2, pct))}%` }} /></div></td></tr>; })}</tbody>
          </table>
        </div>
      </Panel>
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="What your wins are teaching LeadX" description="Recommendations are derived from your recorded outcomes; scoring rules are not silently changed.">
          <dl className="grid grid-cols-3 divide-x divide-line rounded-lg border border-line">
            {[["Outcomes", learning?.outcomes || 0], ["Won", learning?.won || 0], ["Win rate", `${learning?.win_rate_pct || 0}%`]].map(([name, value]) => <div className="px-4 py-3" key={String(name)}><dt className="stat-label">{name}</dt><dd className="mt-1.5 text-xl font-semibold tabular-nums text-ink">{value}</dd></div>)}
          </dl>
        </Panel>
        <Panel title="Patterns to test next" description="Lookalike discovery queries from your won outcomes.">
          <div className="flex flex-wrap gap-2">{(learning?.lookalike_queries || []).map((query: string) => <Badge tone="accent" key={query}>{query}</Badge>)}{!(learning?.lookalike_queries || []).length && <span className="text-sm text-ink-faint">Record more outcomes to unlock lookalike guidance.</span>}</div>
        </Panel>
      </div>
    </> : null}
  </div>;
}
