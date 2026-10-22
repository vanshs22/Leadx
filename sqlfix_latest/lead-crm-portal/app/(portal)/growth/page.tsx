"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { crm, crmExtras, crmIntelligence, type DashboardResponse, type PipelineBoard, type ReportsSummary } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/States";
import { Icon, type IconName } from "@/components/ui/Icon";

type GrowthData = {
  dashboard: DashboardResponse | null;
  quota: any;
  requests: any[];
  reports: ReportsSummary | null;
  pipeline: PipelineBoard | null;
  sequences: any[];
  replies: any[];
  tasks: any;
  deals: any;
  email: any;
};

const EMPTY_DATA: GrowthData = {
  dashboard: null,
  quota: null,
  requests: [],
  reports: null,
  pipeline: null,
  sequences: [],
  replies: [],
  tasks: null,
  deals: null,
  email: null,
};

const targetDefaults = {
  vertical: "medspa",
  geo: "Hoboken, New Jersey",
  keywords: "med spa, botox, aesthetics",
  offer: "lead_gen_service",
  requested: 15,
};

function result<T>(settled: PromiseSettledResult<T>, fallback: T): T {
  return settled.status === "fulfilled" ? settled.value : fallback;
}

function statusClass(status: string) {
  if (["approved", "completed", "delivered", "won", "active"].includes(status)) return "bg-signal-50 text-signal-700 dark:bg-signal-500/15 dark:text-signal-300";
  if (["rejected", "failed", "lost"].includes(status)) return "bg-rust-50 text-rust-700 dark:bg-rust-900/30 dark:text-rust-300";
  return "bg-surface-3 text-ink-muted";
}

function Metric({ icon, label, value, hint, href }: { icon: IconName; label: string; value: string | number; hint: string; href?: string }) {
  const content = <div className="data-card data-card-hover p-4"><div className="flex items-start justify-between gap-3"><div><div className="stat-label">{label}</div><div className="metric-number mt-2">{value}</div></div><span className="rounded-xl bg-brand-50 p-2 text-brand-600 dark:bg-brand-500/15 dark:text-brand-300"><Icon name={icon} size={17} /></span></div><div className="mt-1 text-xs text-ink-faint">{hint}</div></div>;
  return href ? <Link href={href}>{content}</Link> : content;
}

export default function GrowthPage() {
  const [data, setData] = useState<GrowthData>(EMPTY_DATA);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [failedPanels, setFailedPanels] = useState<string[]>([]);
  const [form, setForm] = useState(targetDefaults);
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState("");

  const refresh = useCallback(async (silent = false) => {
    const tenantId = getTenantId();
    if (!tenantId) {
      setError(new Error("Your workspace is still loading. Please try again."));
      setLoading(false);
      return;
    }
    if (silent) setRefreshing(true); else setLoading(true);
    setError(null);
    const calls = await Promise.allSettled([
      crm.dashboard(tenantId),
      crm.searchQuota(tenantId),
      crm.discoveryRequests(tenantId),
      crm.reportsSummary(tenantId),
      crm.pipeline(tenantId),
      crm.sequences(tenantId),
      crm.replies(tenantId),
      crmIntelligence.tasks(tenantId, 20),
      crmExtras.dealReport(tenantId, 365),
      crmExtras.emailHealth(tenantId),
    ]);
    const names = ["dashboard", "quota", "discovery", "quality", "pipeline", "sequences", "replies", "tasks", "deals", "delivery"];
    setFailedPanels(names.filter((_, i) => calls[i].status === "rejected"));
    const [dashboard, quota, requests, reports, pipeline, sequences, replies, tasks, deals, email] = calls;
    const next: GrowthData = {
      dashboard: result(dashboard, null),
      quota: result(quota, null),
      requests: result(requests, { requests: [] } as any)?.requests || [],
      reports: result(reports, null),
      pipeline: result(pipeline, null),
      sequences: result(sequences, { sequences: [] } as any)?.sequences || [],
      replies: result(replies, { replies: [] } as any)?.replies || [],
      tasks: result(tasks, null),
      deals: result(deals, null),
      email: result(email, null),
    };
    setData(next);
    if (calls.every((call) => call.status === "rejected")) setError(new Error("LeadX could not load workspace growth data."));
    setLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  useCrmChange(() => { void refresh(true); });

  async function requestDiscovery(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setNotice("");
    setError(null);
    try {
      const request = await crm.createDiscoveryRequest(getTenantId(), {
        vertical: form.vertical.trim(),
        geo: form.geo.split(",").map((item) => item.trim()).filter(Boolean),
        keywords: form.keywords.split(",").map((item) => item.trim()).filter(Boolean),
        offer_category: form.offer.trim() || "lead_gen_service",
        requested_leads: Math.max(1, Number(form.requested) || 1),
        name: `${form.vertical.trim()} — ${form.geo.trim()}`,
        save_icp: true,
      });
      setNotice(request.message || "Target saved. Your discovery request is awaiting approval.");
      await refresh(true);
    } catch (e) {
      setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setSubmitting(false);
    }
  }

  const dashboard = data.dashboard;
  const kpis = dashboard?.kpis;
  const latestRequest = data.requests[0];
  const quota = data.quota;
  const recentReplies = data.replies.slice(0, 4);
  const pipelineStages = data.pipeline?.stages?.slice(0, 4) || [];
  const overdue = Number(data.tasks?.overdue || 0);
  const openDeals = Number(data.deals?.open_deals || 0);
  const quality = data.reports;
  const delivery = data.email;
  const requestCounts = useMemo(() => {
    return data.requests.reduce((acc: Record<string, number>, request: any) => {
      const key = request.status || "queued";
      acc[key] = (acc[key] || 0) + 1;
      return acc;
    }, {});
  }, [data.requests]);

  if (loading && !dashboard) {
    return <div className="page"><div className="skeleton h-10 w-64" /><div className="skeleton mt-2 h-5 w-96" /><div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-5"><LoadingRows rows={5} cols={2} /></div><div className="mt-6 grid gap-5 lg:grid-cols-[1.1fr_.9fr]"><div className="card-pad min-h-72"><div className="skeleton h-5 w-48" /></div><div className="card-pad min-h-72"><div className="skeleton h-5 w-40" /></div></div></div>;
  }

  return (
    <div className="page">
      <header className="hero-panel overflow-hidden p-5 md:p-7">
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div className="max-w-3xl">
            <div className="eyebrow flex items-center gap-2 text-brand-700 dark:text-brand-300"><span className="live-dot" /> Growth workspace</div>
            <h1 className="page-title mt-2">Turn a target into pipeline.</h1>
            <p className="page-sub">Configure who you want, reuse the verified database before fresh discovery, and monitor every handoff from evidence to reply to revenue.</p>
          </div>
          <div className="flex items-center gap-2"><span className="pill border border-line bg-surface-2 text-ink-muted">{refreshing ? "Syncing" : "Live workspace"}</span><button type="button" className="btn-secondary" onClick={() => void refresh(true)} disabled={refreshing}>Refresh</button></div>
        </div>
        {failedPanels.length > 0 && <div className="mt-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-300/60 bg-amber-50/70 px-3 py-2.5 text-xs text-amber-900 dark:bg-amber-500/10 dark:text-amber-200"><span><b>Some signals are degraded.</b> Core CRM data remains available; affected panels will recover on the next refresh.</span><button type="button" className="font-semibold underline" onClick={() => void refresh(true)}>Retry</button></div>}
      </header>

      {error && <ErrorState error={error} onRetry={() => void refresh()} />}
      {notice && <div role="status" className="rounded-xl border border-signal-300/60 bg-signal-50/70 px-4 py-3 text-sm text-signal-800 dark:bg-signal-500/10 dark:text-signal-200">{notice}</div>}

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        <Metric icon="leads" label="Leads ready" value={kpis?.total_leads ?? "—"} hint="Assigned and ready to work" href="/leads" />
        <Metric icon="growth" label="Tier A signal" value={kpis?.tier_a ?? "—"} hint="Highest-confidence opportunities" href="/leads" />
        <Metric icon="pipeline" label="In pipeline" value={kpis?.in_pipeline ?? "—"} hint={`${openDeals} open deals`} href="/pipeline" />
        <Metric icon="reply" label="Replies" value={data.replies.length} hint="Sort and route intent" href="/replies" />
        <Metric icon="revenue" label="Quota remaining" value={quota?.remaining ?? "—"} hint={quota ? `${quota.used} used · ${quota.reserved} reserved` : "Monthly allowance"} href="/search" />
      </section>

      <section className="grid gap-5 lg:grid-cols-[1.1fr_.9fr]">
        <form onSubmit={requestDiscovery} className="data-card p-5 md:p-6">
          <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="section-kicker text-brand-700 dark:text-brand-300">01 · Target engine</div><h2 className="mt-1 text-lg font-semibold text-ink">Set the next target</h2><p className="mt-1 text-sm text-ink-muted">Requests are approval-gated and saved as an ICP for repeatable discovery.</p></div><Icon name="search" className="text-brand-600" /></div>
          <div className="mt-5 grid gap-3 sm:grid-cols-2">
            <label className="label">Vertical<input className="input mt-1" value={form.vertical} onChange={(e) => setForm({ ...form, vertical: e.target.value })} /></label>
            <label className="label">Locations<input className="input mt-1" value={form.geo} onChange={(e) => setForm({ ...form, geo: e.target.value })} /></label>
            <label className="label sm:col-span-2">Search signals<input className="input mt-1" value={form.keywords} onChange={(e) => setForm({ ...form, keywords: e.target.value })} /></label>
            <label className="label">Offer category<input className="input mt-1" value={form.offer} onChange={(e) => setForm({ ...form, offer: e.target.value })} /></label>
            <label className="label">Leads requested<input className="input mt-1" type="number" min={1} max={quota?.remaining || 500} value={form.requested} onChange={(e) => setForm({ ...form, requested: Number(e.target.value) || 1 })} /></label>
          </div>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-brand-200/70 bg-brand-50/60 px-3 py-2.5 text-xs text-brand-900 dark:border-brand-500/20 dark:bg-brand-500/10 dark:text-brand-200"><span><b>Reuse first.</b> LeadX checks existing verified inventory before requesting fresh enrichment.</span><button className="btn-primary" disabled={submitting}>{submitting ? "Saving target…" : "Request opportunities"}</button></div>
        </form>

        <div className="data-card p-5 md:p-6">
          <div className="flex items-start justify-between gap-3"><div><div className="section-kicker">02 · Discovery health</div><h2 className="mt-1 text-lg font-semibold text-ink">Evidence before delivery</h2></div><Icon name="brain" className="text-signal-600" /></div>
          <div className="mt-5 grid grid-cols-2 gap-3">
            <div className="action-card"><div className="stat-label">With email</div><div className="mt-1 text-xl font-semibold text-ink">{quality ? `${quality.with_email_pct}%` : "—"}</div><div className="text-[11px] text-ink-faint">{quality?.with_email ?? 0} contacts</div></div>
            <div className="action-card"><div className="stat-label">Avg score</div><div className="mt-1 text-xl font-semibold text-ink">{quality?.avg_score ?? "—"}</div><div className="text-[11px] text-ink-faint">Fit + opportunity</div></div>
            <div className="action-card"><div className="stat-label">Existing matches</div><div className="mt-1 text-xl font-semibold text-ink">{latestRequest?.global_stored_count ?? "—"}</div><div className="text-[11px] text-ink-faint">Reused inventory</div></div>
            <div className="action-card"><div className="stat-label">Freshly enriched</div><div className="mt-1 text-xl font-semibold text-ink">{latestRequest?.enriched_count ?? "—"}</div><div className="text-[11px] text-ink-faint">Current evidence</div></div>
          </div>
          <Link href="/intelligence" className="mt-4 inline-flex items-center gap-1 text-xs font-semibold text-brand-600 hover:underline">Review lead evidence <Icon name="arrow" size={14} /></Link>
        </div>
      </section>

      <section className="grid gap-5 lg:grid-cols-[1.2fr_.8fr]">
        <div className="data-card overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4"><div><div className="section-kicker">03 · Target runs</div><h2 className="mt-1 text-lg font-semibold text-ink">Discovery requests</h2></div><Link href="/search" className="btn-secondary">Open discover</Link></div>
          <div className="grid gap-3 p-5 sm:grid-cols-4">{Object.entries(requestCounts).slice(0, 4).map(([name, count]) => <div key={name} className="action-card"><div className="stat-label">{name}</div><div className="mt-1 text-xl font-semibold text-ink">{count}</div></div>)}</div>
          {data.requests.length === 0 ? <EmptyState title="No target runs yet" hint="Submit a target above to start an approval-gated discovery request." icon={<Icon name="search" />} /> : <div className="divide-y divide-line/70 border-t border-line/70">{data.requests.slice(0, 5).map((request: any) => <div key={request.id} className="flex flex-wrap items-center justify-between gap-3 px-5 py-3.5"><div><div className="text-sm font-semibold text-ink">{request.vertical} <span className="font-normal text-ink-faint">· {(request.locations || []).join(", ") || "Any location"}</span></div><div className="mt-1 text-xs text-ink-faint">{request.requested_leads} requested · {request.assigned_count || 0} assigned · {request.enriched_count || 0} enriched</div></div><span className={`pill ${statusClass(request.status || "queued")}`}>{request.status || "queued"}</span></div>)}</div>}
        </div>

        <div className="data-card p-5">
          <div className="section-kicker">04 · Delivery guardrails</div><h2 className="mt-1 text-lg font-semibold text-ink">Outbound readiness</h2>
          <div className="mt-4 space-y-3"><div className="flex items-center justify-between rounded-xl border border-line bg-surface-3/40 p-3"><span className="text-sm text-ink-muted">Sending today</span><b className="text-ink">{delivery ? `${delivery.sent_today} / ${delivery.daily_cap}` : "—"}</b></div><div className="flex items-center justify-between rounded-xl border border-line bg-surface-3/40 p-3"><span className="text-sm text-ink-muted">Can send now</span><span className={`pill ${delivery?.can_send_now ? "bg-signal-50 text-signal-700 dark:bg-signal-500/15 dark:text-signal-300" : "bg-surface-3 text-ink-muted"}`}>{delivery ? (delivery.can_send_now ? "Ready" : "Paused") : "—"}</span></div><div className="flex items-center justify-between rounded-xl border border-line bg-surface-3/40 p-3"><span className="text-sm text-ink-muted">Sequences</span><b className="text-ink">{data.sequences.length} configured</b></div></div>
          <div className="mt-4 flex flex-wrap gap-2"><Link href="/sequences" className="btn-secondary">Sequences</Link><Link href="/settings/email" className="btn-ghost">Email settings</Link></div>
        </div>
      </section>

      <section className="grid gap-5 lg:grid-cols-[1.15fr_.85fr]">
        <div className="data-card p-5"><div className="flex items-center justify-between gap-3"><div><div className="section-kicker">05 · Revenue motion</div><h2 className="mt-1 text-lg font-semibold text-ink">Pipeline and deals</h2></div><Link href="/pipeline" className="text-xs font-semibold text-brand-600 hover:underline">Open pipeline →</Link></div><div className="mt-4 grid gap-3 sm:grid-cols-2">{pipelineStages.map((stage) => <div key={stage.id} className="action-card"><div className="flex items-center justify-between gap-2"><span className="truncate text-sm font-semibold text-ink">{stage.name}</span><span className="pill bg-surface-3 text-ink-muted">{stage.leads?.length || 0}</span></div><div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-3"><div className="h-full rounded-full bg-brand-500" style={{ width: `${Math.min(100, (stage.leads?.length || 0) * 12)}%` }} /></div></div>)}{pipelineStages.length === 0 && <EmptyState title="Pipeline is ready when leads arrive" hint="Move qualified leads through stages from the Pipeline drill-down." />}</div><div className="mt-4 flex flex-wrap gap-3 text-xs text-ink-muted"><span>Open value <b className="text-ink">{data.deals ? `$${Number(data.deals.open_value || 0).toLocaleString()}` : "—"}</b></span><span>Weighted <b className="text-ink">{data.deals ? `$${Number(data.deals.forecast_weighted || 0).toLocaleString()}` : "—"}</b></span><span>Win rate <b className="text-ink">{data.deals?.win_rate ?? "—"}</b></span></div></div>
        <div className="data-card p-5"><div className="flex items-center justify-between gap-3"><div><div className="section-kicker">06 · Human follow-through</div><h2 className="mt-1 text-lg font-semibold text-ink">Replies and tasks</h2></div><Icon name="activity" className="text-brand-600" /></div><div className="mt-4 rounded-xl border border-line bg-surface-3/40 p-3"><div className="stat-label">SLA queue</div><div className="mt-1 text-2xl font-semibold text-ink">{overdue}</div><div className="text-xs text-ink-faint">overdue tasks</div></div><div className="mt-4 space-y-2">{recentReplies.length === 0 ? <p className="text-sm text-ink-faint">No replies yet. Responses will appear here when sequences start conversations.</p> : recentReplies.map((reply: any) => <Link key={reply.id} href={reply.assignment_id ? `/leads/${reply.assignment_id}` : "/replies"} className="block rounded-xl border border-line p-3 hover:border-brand-300/70"><div className="flex items-center justify-between gap-2"><span className={`pill ${statusClass(reply.category || "unclassified")}`}>{reply.category || "unclassified"}</span><span className="truncate text-xs text-ink-faint">{reply.from_email}</span></div><div className="mt-1 truncate text-sm font-medium text-ink">{reply.subject || "New reply"}</div></Link>)}</div><div className="mt-4 flex gap-2"><Link href="/replies" className="btn-secondary">Review replies</Link><Link href="/activities" className="btn-ghost">Open tasks</Link></div></div>
      </section>
    </div>
  );
}
