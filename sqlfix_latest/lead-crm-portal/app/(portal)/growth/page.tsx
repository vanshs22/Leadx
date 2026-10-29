"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { crm, crmExtras, crmIntelligence, type DashboardResponse, type PipelineBoard, type ReportsSummary } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { useLiveLoader } from "@/lib/live";
import { OperationStateCard, toOperationState } from "@/components/ui/OperationState";
import { EmptyState, ErrorState, LoadingRows, PollingDeadlineState } from "@/components/ui/States";
import { Icon } from "@/components/ui/Icon";
import { Badge, Notice, PageHeader, Panel, QuietBreadcrumbs, StatCard, StatGrid, StatusBadge } from "@/components/ui/Primitives";

type GrowthData = {
  dashboard: DashboardResponse | null;
  quota: any;
  requests: any[];
  latestDiscovery: any;
  reports: ReportsSummary | null;
  pipeline: PipelineBoard | null;
  sequences: any[];
  replies: any[];
  tasks: any;
  deals: any;
  email: any;
  learning: any;
};

const EMPTY_DATA: GrowthData = {
  dashboard: null,
  quota: null,
  requests: [],
  latestDiscovery: null,
  reports: null,
  pipeline: null,
  sequences: [],
  replies: [],
  tasks: null,
  deals: null,
  email: null,
  learning: null,
};

const targetDefaults = {
  vertical: "medspa",
  geo: "Hoboken, New Jersey",
  keywords: "med spa, botox, aesthetics",
  offer: "lead_gen_service",
  exclusivity: "shared" as "shared" | "exclusive",
  requested: 15,
};

const WITHDRAWABLE_REQUEST_STATUSES = new Set(["pending_approval", "approved", "queued", "running"]);

function result<T>(settled: PromiseSettledResult<T>, fallback: T): T {
  return settled.status === "fulfilled" ? settled.value : fallback;
}

function fmt(value: unknown) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  return Number.isFinite(n) ? n.toLocaleString() : String(value);
}

function money(value: unknown) {
  return `$${Number(value || 0).toLocaleString()}`;
}

/** One step of the latest-run flow inside the growth banner. */
function FlowStep({ label, value, hint, last = false }: { label: string; value: string; hint: string; last?: boolean }) {
  return (
    <li className="relative min-w-0 rounded-lg border border-white/10 bg-white/[0.04] px-4 py-3">
      <div className="eyebrow">{label}</div>
      <div className="mt-1.5 font-display text-2xl font-semibold tabular-nums text-ink">{value}</div>
      <div className="mt-1 truncate text-xs text-ink-muted">{hint}</div>
      {!last ? (
        <span className="absolute -right-2.5 top-1/2 z-10 hidden -translate-y-1/2 text-ink-faint lg:block" aria-hidden="true">
          <Icon name="chevron" size={16} />
        </span>
      ) : null}
    </li>
  );
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
  const [pollingExpired, setPollingExpired] = useState(false);
  const refreshGeneration = useRef(0);

  const refresh = useCallback(async (silent = false) => {
    const generation = ++refreshGeneration.current;
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
      crmIntelligence.learning(tenantId),
    ]);
    if (generation !== refreshGeneration.current) return;
    const names = ["dashboard", "quota", "discovery", "quality", "pipeline", "sequences", "replies", "tasks", "deals", "delivery", "learning"];
    setFailedPanels(names.filter((_, i) => calls[i].status === "rejected"));
    const [dashboard, quota, requests, reports, pipeline, sequences, replies, tasks, deals, email, learning] = calls;
    const requestRows = result(requests, { requests: [] } as any)?.requests || [];
    let latestDiscovery = null;
    if (requestRows[0]?.id) {
      try { latestDiscovery = await crm.discoveryRequestStatus(tenantId, requestRows[0].id); } catch { latestDiscovery = null; }
      if (generation !== refreshGeneration.current) return;
    }
    const next: GrowthData = {
      dashboard: result(dashboard, null),
      quota: result(quota, null),
      requests: requestRows,
      latestDiscovery,
      reports: result(reports, null),
      pipeline: result(pipeline, null),
      sequences: result(sequences, { sequences: [] } as any)?.sequences || [],
      replies: result(replies, { replies: [] } as any)?.replies || [],
      tasks: result(tasks, null),
      deals: result(deals, null),
      email: result(email, null),
      learning: result(learning, null),
    };
    setData(next);
    if (calls.every((call) => call.status === "rejected")) setError(new Error("LeadX could not load workspace growth data."));
    setLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  useLiveLoader(() => refresh(true), 30_000, [refresh], { runImmediately: false, onDeadline: () => setPollingExpired(true) });
  useCrmChange(() => { void refresh(true); }, { tables: ["discovery_requests", "pipeline_runs", "jobs", "lead_assignments", "lead_quality_gates", "lead_verification_observations", "lead_score_snapshots", "quota_usage", "email_replies", "notifications"] });

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
        exclusivity: form.exclusivity,
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

  async function withdrawRequest(requestId: string) {
    if (!window.confirm("Withdraw this opportunity request? Queued or running work will be stopped and unused quota released.")) return;
    setSubmitting(true);
    setNotice("");
    setError(null);
    try {
      const result = await crm.withdrawDiscoveryRequest(getTenantId(), requestId);
      setNotice(result.message || "Opportunity request withdrawn.");
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
  const latestOperation = data.latestDiscovery ? toOperationState(data.latestDiscovery, { status: latestRequest?.status || "queued", requestId: latestRequest?.id }) : null;
  const quota = data.quota;
  const recentReplies = data.replies.slice(0, 4);
  const pipelineStages = data.pipeline?.stages?.slice(0, 4) || [];
  const overdue = Number(data.tasks?.overdue || 0);
  const openDeals = Number(data.deals?.open_deals || 0);
  const quality = data.reports;
  const delivery = data.email;
  const recommendations = (data.learning?.recommendations || []).filter((item: any) => item.status !== "rejected").slice(0, 3);
  const requestCounts = useMemo(() => {
    return data.requests.reduce((acc: Record<string, number>, request: any) => {
      const key = request.status || "queued";
      acc[key] = (acc[key] || 0) + 1;
      return acc;
    }, {});
  }, [data.requests]);

  if (loading && !dashboard) {
    return (
      <div className="page" aria-busy="true" aria-live="polite">
        <span className="sr-only">Loading growth workspace…</span>
        <div className="page-header" aria-hidden="true">
          <div className="min-w-0 flex-1">
            <div className="skeleton h-3 w-28" />
            <div className="skeleton mt-2.5 h-7 w-72 max-w-full" />
            <div className="skeleton mt-2.5 h-4 w-[30rem] max-w-full" />
          </div>
          <div className="skeleton h-9 w-24" />
        </div>
        <div className="skeleton h-36 rounded-xl" aria-hidden="true" />
        <div className="panel"><LoadingRows rows={5} cols={2} /></div>
        <div className="grid gap-4 lg:grid-cols-3" aria-hidden="true">
          <div className="panel min-h-72 p-5 lg:col-span-2"><div className="skeleton h-5 w-48" /></div>
          <div className="panel min-h-72 p-5"><div className="skeleton h-5 w-40" /></div>
        </div>
      </div>
    );
  }

  const quotaBlocked = Boolean(quota && quota.remaining < 1);

  return (
    <div className="page">
      <PageHeader
        eyebrow={<QuietBreadcrumbs items={[{ label: "Workspace", href: "/dashboard" }, { label: "Growth" }]} />}
        title="Turn a target into pipeline"
        description="Configure who you want, reuse the verified database before fresh discovery, and monitor every handoff from evidence to reply to revenue."
        actions={
          <button type="button" aria-label="Refresh growth workspace" className="btn-secondary" onClick={() => void refresh(true)} disabled={refreshing}>
            <Icon name="refresh" size={14} />
            Refresh
          </button>
        }
        meta={
          <>
            <span className="inline-flex items-center gap-1.5" role="status" aria-live="polite">
              <span className="live-dot" aria-hidden="true" />
              {refreshing ? "Syncing" : "Live workspace"}
            </span>
            {quota ? (
              <span className="tabular-nums">
                {fmt(quota.remaining)} of {fmt(quota.monthly_limit ?? quota.limit)} monthly leads available
              </span>
            ) : null}
          </>
        }
      />

      {failedPanels.length > 0 && (
        <div className="notice notice-warning items-center" role="status">
          <span>
            <b className="font-semibold">Some signals are degraded.</b> Core CRM data remains available; affected panels will recover on the next refresh.
          </span>
          <button type="button" aria-label="Retry degraded growth panels" className="btn-secondary btn-sm shrink-0" onClick={() => void refresh(true)}>Retry</button>
        </div>
      )}
      {error && <ErrorState error={error} onRetry={() => void refresh()} />}
      {pollingExpired && <PollingDeadlineState onRefresh={() => { setPollingExpired(false); void refresh(true); }} />}
      {notice && <Notice tone="success">{notice}</Notice>}
      {latestOperation && <OperationStateCard operation={latestOperation} title="Latest discovery run" onRefresh={() => { setPollingExpired(false); void refresh(true); }} />}

      <section className="growth-banner overflow-hidden p-5 md:p-6" aria-labelledby="growth-flow-title">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="eyebrow">Latest target run</div>
            <h2 id="growth-flow-title" className="mt-1.5 font-display text-lg font-semibold text-ink">
              {latestRequest ? (
                <>
                  {latestRequest.vertical || "Target"}
                  <span className="font-normal text-ink-muted"> · {(latestRequest.locations || []).join(", ") || "Any location"}</span>
                </>
              ) : (
                "No target runs yet"
              )}
            </h2>
            <p className="mt-1 max-w-2xl text-[0.8125rem] leading-6 text-ink-muted">
              {latestRequest
                ? "Existing verified inventory is reused first; only the gap is sent to fresh enrichment before assignment."
                : "Set your first target below. LeadX checks verified inventory before requesting fresh enrichment."}
            </p>
          </div>
          {latestRequest ? (
            <span className="inline-flex items-center gap-1.5 rounded-md border border-white/15 bg-white/[0.06] px-2 py-1 text-xs font-semibold capitalize text-ink">
              <span className="h-1.5 w-1.5 rounded-full bg-signal-300" aria-hidden="true" />
              {String(latestRequest.status || "queued").replace(/_/g, " ")}
            </span>
          ) : null}
        </div>
        <ol className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label="Latest run lead flow">
          <FlowStep label="Requested" value={fmt(latestRequest?.requested_leads)} hint="Leads asked for" />
          <FlowStep label="Existing matches" value={fmt(latestRequest?.global_stored_count)} hint="Reused verified inventory" />
          <FlowStep label="Freshly enriched" value={fmt(latestRequest?.enriched_count)} hint="New evidence gathered" />
          <FlowStep label="Assigned" value={fmt(latestRequest ? latestRequest.assigned_count || 0 : null)} hint="Delivered to your queue" last />
        </ol>
      </section>

      <StatGrid cols={5}>
        <StatCard icon={<Icon name="leads" size={16} />} label="Leads ready" value={fmt(kpis?.total_leads)} hint="Assigned and ready to work" href="/leads" tone="brand" />
        <StatCard icon={<Icon name="spark" size={16} />} label="Tier A signal" value={fmt(kpis?.tier_a)} hint="Highest-confidence opportunities" href="/leads" tone="accent" />
        <StatCard icon={<Icon name="pipeline" size={16} />} label="In pipeline" value={fmt(kpis?.in_pipeline)} hint={<span className="tabular-nums">{openDeals.toLocaleString()} open deals</span>} href="/pipeline" tone="brand" />
        <StatCard icon={<Icon name="reply" size={16} />} label="Replies" value={fmt(data.replies.length)} hint="Sort and route intent" href="/replies" tone="success" />
        <StatCard
          icon={<Icon name="revenue" size={16} />}
          label="Quota remaining"
          value={fmt(quota?.remaining)}
          hint={quota ? <span className="tabular-nums">{fmt(quota.used)} used · {fmt(quota.reserved)} reserved</span> : "Monthly allowance"}
          href="/search"
          tone={quotaBlocked ? "warn" : "neutral"}
        />
      </StatGrid>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel
          id="growth-target"
          className="lg:col-span-2"
          title="Set the next target"
          description="Requests are approval-gated and saved as an ICP for repeatable discovery."
          actions={<Badge tone="neutral">Approval required</Badge>}
        >
          <form onSubmit={requestDiscovery} aria-labelledby="growth-target-title">
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block"><span className="field-label">Vertical</span><input className="input" value={form.vertical} onChange={(e) => setForm({ ...form, vertical: e.target.value })} /></label>
              <label className="block"><span className="field-label">Locations</span><input className="input" value={form.geo} onChange={(e) => setForm({ ...form, geo: e.target.value })} /><span className="field-hint block">Comma-separated cities or regions.</span></label>
              <label className="block sm:col-span-2"><span className="field-label">Search signals</span><input className="input" value={form.keywords} onChange={(e) => setForm({ ...form, keywords: e.target.value })} /><span className="field-hint block">Comma-separated keywords used to find matching businesses.</span></label>
              <label className="block"><span className="field-label">Offer category</span><input className="input" value={form.offer} onChange={(e) => setForm({ ...form, offer: e.target.value })} /></label>
              <label className="block"><span className="field-label">Delivery policy</span><select className="select" value={form.exclusivity} onChange={(e) => setForm({ ...form, exclusivity: e.target.value as "shared" | "exclusive" })}><option value="shared">Shared — recommended</option><option value="exclusive">Exclusive — same offer + territory lock</option></select></label>
              <label className="block"><span className="field-label">Leads requested</span><input className="input tabular-nums" type="number" min={1} max={500}
              value={form.requested}
              onChange={(e) => setForm({ ...form, requested: Math.min(500, Math.max(1, Number(e.target.value) || 1)) })} /></label>
            </div>
            <div className="mt-5 flex flex-col gap-3 border-t border-line pt-4 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-xs leading-5 text-ink-muted">
                <span className="font-semibold text-ink">Reuse first.</span> LeadX checks existing verified inventory before requesting fresh enrichment.{" "}
                {quota && <span className="tabular-nums">{fmt(quota.remaining)} of {fmt(quota.monthly_limit ?? quota.limit)} monthly leads available after {fmt(quota.reserved)} reserved.</span>}
              </p>
              <button type="submit" className="btn-primary shrink-0" disabled={submitting || quotaBlocked}>{submitting ? "Saving target…" : quotaBlocked ? "Quota unavailable" : "Request opportunities"}</button>
            </div>
          </form>
        </Panel>

        <Panel
          id="growth-evidence"
          title="Evidence before delivery"
          description="Discovery health across your workspace."
          footer={<Link href="/intelligence" className="link inline-flex items-center gap-1">Review lead evidence <Icon name="arrow" size={14} /></Link>}
        >
          <dl className="grid grid-cols-2 gap-x-4 gap-y-5">
            <div><dt className="stat-label">With email</dt><dd className="mt-1.5 font-display text-xl font-semibold tabular-nums text-ink">{quality ? `${quality.with_email_pct}%` : "—"}</dd><dd className="text-xs tabular-nums text-ink-faint">{fmt(quality?.with_email ?? 0)} contacts</dd></div>
            <div><dt className="stat-label">Avg score</dt><dd className="mt-1.5 font-display text-xl font-semibold tabular-nums text-ink">{quality?.avg_score ?? "—"}</dd><dd className="text-xs text-ink-faint">Fit + opportunity</dd></div>
            <div><dt className="stat-label">Existing matches</dt><dd className="mt-1.5 font-display text-xl font-semibold tabular-nums text-ink">{fmt(latestRequest?.global_stored_count)}</dd><dd className="text-xs text-ink-faint">Reused inventory</dd></div>
            <div><dt className="stat-label">Freshly enriched</dt><dd className="mt-1.5 font-display text-xl font-semibold tabular-nums text-ink">{fmt(latestRequest?.enriched_count)}</dd><dd className="text-xs text-ink-faint">Current evidence</dd></div>
          </dl>
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel
          id="growth-requests"
          className="lg:col-span-2"
          title="Discovery requests"
          description={
            Object.keys(requestCounts).length ? (
              <span className="mt-1.5 flex flex-wrap gap-1.5">
                {Object.entries(requestCounts).slice(0, 4).map(([name, count]) => (
                  <StatusBadge key={name} status={name} label={<span className="tabular-nums">{name.replace(/_/g, " ")} · {count}</span>} />
                ))}
              </span>
            ) : "Approval-gated target runs and their delivery progress."
          }
          actions={<Link href="/search" className="btn-secondary btn-sm">Open discover</Link>}
          bodyClassName="p-0"
        >
          {data.requests.length === 0 ? (
            <EmptyState title="No target runs yet" hint="Submit a target above to start an approval-gated discovery request." icon={<Icon name="search" />} />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <caption className="sr-only">Recent discovery requests</caption>
                <thead>
                  <tr>
                    <th scope="col">Target</th>
                    <th scope="col" className="!text-right">Requested</th>
                    <th scope="col" className="!text-right">Assigned</th>
                    <th scope="col" className="!text-right">Enriched</th>
                    <th scope="col">Status</th>
                    <th scope="col" className="!text-right">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {data.requests.slice(0, 5).map((request: any) => (
                    <tr key={request.id}>
                      <td className="max-w-[280px]">
                        <div className="truncate font-medium text-ink">{request.vertical}</div>
                        <div className="truncate text-xs text-ink-faint">{(request.locations || []).join(", ") || "Any location"}</div>
                      </td>
                      <td className="text-right tabular-nums">{fmt(request.requested_leads)}</td>
                      <td className="text-right tabular-nums">{fmt(request.assigned_count || 0)}</td>
                      <td className="text-right tabular-nums">{fmt(request.enriched_count || 0)}</td>
                      <td><StatusBadge status={request.status || "queued"} /></td>
                      <td className="text-right">
                        {WITHDRAWABLE_REQUEST_STATUSES.has(String(request.status || "").toLowerCase()) ? (
                          <button type="button" className="btn-ghost btn-sm text-rust-600 dark:text-rust-300" onClick={() => void withdrawRequest(request.id)} disabled={submitting}>Withdraw</button>
                        ) : <span className="text-xs text-ink-faint">—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <Panel
          id="growth-delivery"
          title="Outbound readiness"
          description="Delivery guardrails for sequences."
          footer={<div className="flex flex-wrap gap-2"><Link href="/sequences" className="btn-secondary btn-sm">Sequences</Link><Link href="/settings/email" className="btn-ghost btn-sm">Email settings</Link></div>}
          bodyClassName="p-0"
        >
          <dl className="divide-y divide-line">
            <div className="flex items-center justify-between gap-3 px-5 py-3"><dt className="text-[0.8125rem] text-ink-muted">Sending today</dt><dd className="font-semibold tabular-nums text-ink">{delivery ? `${fmt(delivery.sent_today)} / ${fmt(delivery.daily_cap)}` : "—"}</dd></div>
            <div className="flex items-center justify-between gap-3 px-5 py-3"><dt className="text-[0.8125rem] text-ink-muted">Can send now</dt><dd>{delivery ? <StatusBadge status={delivery.can_send_now ? "active" : "paused"} label={delivery.can_send_now ? "Ready" : "Paused"} /> : <span className="text-ink-faint">—</span>}</dd></div>
            <div className="flex items-center justify-between gap-3 px-5 py-3"><dt className="text-[0.8125rem] text-ink-muted">Sequences</dt><dd className="font-semibold tabular-nums text-ink">{data.sequences.length} configured</dd></div>
          </dl>
        </Panel>
      </div>

      <Panel
        id="growth-learning"
        title="Let wins improve the next search"
        description="Recommendations stay in review until you approve them. Approved query expansions affect future discovery only; your ICP is never changed silently."
        actions={<Badge tone="neutral" className="tabular-nums">{fmt(data.learning?.won ?? 0)} wins · {fmt(data.learning?.booked ?? 0)} meetings</Badge>}
      >
        {recommendations.length === 0 ? (
          <EmptyState title="No recommendations yet" hint="Record a booked, won, or lost outcome in the CRM to generate tenant-specific recommendations." icon={<Icon name="brain" size={18} />} />
        ) : (
          <div className="grid gap-3 md:grid-cols-3">
            {recommendations.map((item: any) => {
              const rec = item.recommendation || {};
              return (
                <article key={item.id} className="flex flex-col rounded-lg border border-line bg-surface-2 p-4">
                  <div className="flex items-center justify-between gap-2">
                    <StatusBadge status={item.status === "proposed" ? "review" : item.status} label={item.status} />
                    <span className="text-xs tabular-nums text-ink-faint">{fmt(item.sample_size)} signals</span>
                  </div>
                  <h3 className="mt-3 text-sm font-semibold text-ink">{rec.vertical || "General"}</h3>
                  <p className="mt-1 flex-1 text-xs leading-5 text-ink-muted">Try: {(rec.queries || []).slice(0, 3).join(", ") || "more like your wins"}</p>
                  {item.status === "proposed" && <button type="button" aria-label={`Approve next-run expansion for ${rec.vertical || "general"}`} className="btn-secondary btn-sm mt-3 w-full" onClick={async () => { try { await crmIntelligence.approveLearningRecommendation(getTenantId(), item.id); await refresh(true); } catch (e) { setError(e instanceof Error ? e : new Error(String(e))); } }}>Approve next-run expansion</button>}
                </article>
              );
            })}
          </div>
        )}
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel
          id="growth-pipeline"
          title="Pipeline and deals"
          description="Revenue motion across your first stages."
          actions={<Link href="/pipeline" className="btn-ghost btn-sm">Open pipeline <Icon name="arrow" size={14} /></Link>}
          footer={
            <div className="flex flex-wrap gap-x-5 gap-y-1 tabular-nums">
              <span>Open value <b className="font-semibold text-ink">{data.deals ? money(data.deals.open_value) : "—"}</b></span>
              <span>Weighted <b className="font-semibold text-ink">{data.deals ? money(data.deals.forecast_weighted) : "—"}</b></span>
              <span>Win rate <b className="font-semibold text-ink">{data.deals?.win_rate ?? "—"}</b></span>
            </div>
          }
        >
          {pipelineStages.length === 0 ? (
            <EmptyState title="Pipeline is ready when leads arrive" hint="Move qualified leads through stages from the Pipeline drill-down." />
          ) : (
            <ul className="space-y-4" aria-label="Pipeline stages">
              {pipelineStages.map((stage) => (
                <li key={stage.id}>
                  <div className="flex items-center justify-between gap-2 text-[0.8125rem]">
                    <span className="truncate font-medium text-ink">{stage.name}</span>
                    <span className="font-semibold tabular-nums text-ink">{fmt(stage.leads?.length || 0)}</span>
                  </div>
                  <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface-3" aria-hidden="true">
                    <div className="h-full rounded-full bg-brand-600 dark:bg-brand-300" style={{ width: `${Math.min(100, (stage.leads?.length || 0) * 12)}%` }} />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel
          id="growth-followthrough"
          title="Replies and tasks"
          description="Human follow-through on live conversations."
          footer={<div className="flex flex-wrap gap-2"><Link href="/replies" className="btn-secondary btn-sm">Review replies</Link><Link href="/activities" className="btn-ghost btn-sm">Open tasks</Link></div>}
          bodyClassName="p-0"
        >
          <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3">
            <div>
              <div className="stat-label">SLA queue</div>
              <div className="text-xs text-ink-faint">overdue tasks</div>
            </div>
            <div className={`font-display text-2xl font-semibold tabular-nums ${overdue ? "text-amber-700 dark:text-amber-300" : "text-ink"}`}>{overdue.toLocaleString()}</div>
          </div>
          {recentReplies.length === 0 ? (
            <p className="px-5 py-6 text-[0.8125rem] text-ink-faint">No replies yet. Responses will appear here when sequences start conversations.</p>
          ) : (
            <ul className="divide-y divide-line" aria-label="Recent replies">
              {recentReplies.map((reply: any) => (
                <li key={reply.id}>
                  <Link href={reply.assignment_id ? `/leads/${reply.assignment_id}` : "/replies"} className="block px-5 py-3 transition hover:bg-surface-3/60">
                    <div className="flex items-center justify-between gap-3">
                      <span className="min-w-0 truncate text-[0.8125rem] font-medium text-ink">{reply.subject || "New reply"}</span>
                      <StatusBadge status={reply.category || "unclassified"} />
                    </div>
                    <div className="mt-0.5 truncate font-mono text-xs text-ink-faint">{reply.from_email}</div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}
