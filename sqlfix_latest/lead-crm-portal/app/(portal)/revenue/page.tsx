"use client";

import { useLiveLoader } from "@/lib/live";
import { EmptyState, ErrorState, LoadingRows, PollingDeadlineState } from "@/components/ui/States";
import { useState } from "react";
import Link from "next/link";
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

  if (loading && !data) return <div className="page"><h1 className="page-title">Revenue intelligence</h1><LoadingRows rows={4} cols={3} /></div>;
  if (error && !data) return <div className="page"><ErrorState error={error} onRetry={refresh} /></div>;

  const funnel = data?.funnel || {};
  const hasData = Object.keys(funnel).length > 0 || Boolean(learning);
  return <div className="page">
    <header className="flex flex-wrap items-end justify-between gap-4"><div><div className="section-kicker text-brand-600">Revenue intelligence</div><h1 className="page-title">From signal to revenue</h1><p className="page-sub">A single view of what LeadX discovers, contacts, converts and ultimately helps close.</p></div><Link href="/reports" className="btn-secondary">Reports →</Link></header>
    {deadlineReached ? <div className="mt-4"><PollingDeadlineState onRefresh={refresh} /></div> : null}
    {error ? <ErrorState error={error} onRetry={refresh} /> : null}
    {!hasData ? <div className="data-card mt-5"><EmptyState title="No revenue outcomes yet" hint="Move a lead through outreach or pipeline stages to start the revenue feedback loop." action={<Link href="/pipeline" className="btn-secondary">Open pipeline</Link>} /></div> : null}
    {hasData ? <>
      <div className="hero-panel p-5 md:p-6"><div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"><div><div className="stat-label">Revenue won</div><div className="metric-number mt-1">${Number(data?.revenue_won || 0).toLocaleString()}</div></div><div><div className="stat-label">Reply rate</div><div className="metric-number mt-1">{data?.reply_rate_pct ?? 0}%</div></div><div><div className="stat-label">Positive rate</div><div className="metric-number mt-1">{data?.positive_rate_pct ?? 0}%</div></div><div><div className="stat-label">Avg won value</div><div className="metric-number mt-1">${Number(data?.avg_won_value || 0).toLocaleString()}</div></div></div></div>
      <section className="data-card p-5"><div className="flex flex-wrap items-center justify-between gap-2"><div><div className="section-kicker">Conversion chain</div><h2 className="mt-1 text-lg font-semibold">Where opportunities move forward</h2></div><span className="pill bg-surface-3 text-ink-muted">Last 90 days</span></div><div className="mt-5 space-y-3">{Object.entries(funnel).map(([key, value]: [string, any]) => { const pct = data?.conversion_pct?.[key] ?? 0; return <div key={key}><div className="flex justify-between text-sm"><span className="font-medium">{labels[key] || key}</span><span className="font-mono text-ink-muted">{value} <span className="text-ink-faint">({pct}%)</span></span></div><div className="mt-1.5 h-2 rounded-full bg-surface-3"><div className="h-full rounded-full bg-brand-500" style={{ width: `${Math.min(100, Math.max(2, pct))}%` }} /></div></div>; })}</div></section>
      <section className="grid gap-4 lg:grid-cols-2"><div className="data-card p-5"><div className="section-kicker">Closed-loop learning</div><h2 className="mt-1 text-lg font-semibold">What your wins are teaching LeadX</h2><p className="mt-2 text-sm text-ink-muted">Recommendations are derived from your recorded outcomes; scoring rules are not silently changed.</p><div className="mt-4 grid grid-cols-3 gap-3">{[["Outcomes", learning?.outcomes || 0], ["Won", learning?.won || 0], ["Win rate", `${learning?.win_rate_pct || 0}%`]].map(([name, value]) => <div className="action-card" key={String(name)}><div className="stat-label">{name}</div><div className="mt-1 text-xl font-semibold">{value}</div></div>)}</div></div><div className="data-card p-5"><div className="section-kicker">Lookalike discovery</div><h2 className="mt-1 text-lg font-semibold">Patterns to test next</h2><div className="mt-3 flex flex-wrap gap-2">{(learning?.lookalike_queries || []).map((query: string) => <span className="pill bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-300" key={query}>{query}</span>)}{!(learning?.lookalike_queries || []).length && <span className="text-sm text-ink-faint">Record more outcomes to unlock lookalike guidance.</span>}</div></div></section>
    </> : null}
  </div>;
}
