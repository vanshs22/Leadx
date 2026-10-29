"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { crm, crmIntelligence, DashboardResponse } from "@/lib/api";
import { getTenantId, getTenantUserId } from "@/lib/tenant";
import { FunnelChart } from "@/components/charts/FunnelChart";
import { useCrmChange } from "@/lib/crmRealtime";
import { useLiveLoader } from "@/lib/live";
import { EmptyState, ErrorState, PollingDeadlineState } from "@/components/ui/States";
import { PageHeader, Panel, QuietBreadcrumbs, StatCard, StatGrid } from "@/components/ui/Primitives";
import { Icon, type IconName } from "@/components/ui/Icon";
import dynamic from "next/dynamic";
const Sparkline = dynamic(() => import("@/components/charts/Sparkline").then((m) => m.Sparkline), { ssr: false, loading: () => <div className="skeleton h-12 w-full rounded-lg" /> });
const TierDonut = dynamic(() => import("@/components/charts/TierDonut").then((m) => m.TierDonut), { ssr: false, loading: () => <div className="skeleton h-12 w-full rounded-lg" /> });

async function loadDashboard(retries = 2): Promise<DashboardResponse> {
  let lastErr: Error | null = null;
  for (let i = 0; i < retries; i++) {
    const tid = getTenantId();
    if (!tid) {
      await new Promise((r) => setTimeout(r, 250 * (i + 1)));
      lastErr = new Error("Workspace not ready yet");
      continue;
    }
    try {
      return await crm.dashboard(tid);
    } catch (e: any) {
      lastErr = e instanceof Error ? e : new Error(String(e?.message || e));
      const msg = (lastErr.message || "").toLowerCase();
      const retryable =
        msg.includes("internal server") ||
        msg.includes("network") ||
        msg.includes("fetch") ||
        msg.includes("502") ||
        msg.includes("503") ||
        msg.includes("timeout");
      if (!retryable || i === retries - 1) throw lastErr;
      await new Promise((r) => setTimeout(r, 250));
    }
  }
  throw lastErr || new Error("Could not load dashboard");
}

export default function DashboardPage() {
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [intel, setIntel] = useState<any>(null);
  const [pollingExpired, setPollingExpired] = useState(false);
  const refreshGeneration = useRef(0);
  const hasDataRef = useRef(false);

  const refresh = useCallback(() => {
    const generation = ++refreshGeneration.current;
    const hasData = hasDataRef.current;
    if (!hasData) setLoading(true); else setRefreshing(true);
    setError(null);
    loadDashboard()
      .then((d) => {
        if (generation !== refreshGeneration.current) return;
        hasDataRef.current = true;
        setData(d);
        setError(null);
        try {
          const key = `leadx:dashboard:${getTenantId() || "unknown"}:${getTenantUserId() || "anon"}`;
          sessionStorage.setItem(key, JSON.stringify(d));
        } catch {}
      })
      .catch((e) => { if (generation === refreshGeneration.current) setError(e); })
      .finally(() => { if (generation === refreshGeneration.current) { setLoading(false); setRefreshing(false); } });
  }, []);

  useEffect(() => {
    try {
      const key = `leadx:dashboard:${getTenantId() || "unknown"}:${getTenantUserId() || "anon"}`;
      const cached = sessionStorage.getItem(key);
      if (cached) { hasDataRef.current = true; setData(JSON.parse(cached)); setLoading(false); }
    } catch {}
    refresh();
    const tid = getTenantId();
    if (tid) Promise.all([crmIntelligence.pipeline(tid).catch(() => null), crmIntelligence.tasks(tid, 20).catch(() => null)])
      .then(([pipeline, tasks]) => setIntel({ pipeline, tasks }));
  }, [refresh]);
  useLiveLoader(refresh, 30_000, [refresh], { runImmediately: false, onDeadline: () => setPollingExpired(true) });

  // Realtime is a cache-invalidation signal; the API remains authoritative.
  useCrmChange(refresh, { tables: ["lead_assignments", "leads_global", "lead_state_history", "lead_score_snapshots", "lead_quality_gates", "lead_verification_observations", "lead_outcome_events", "lead_activities", "activities", "pipeline_stages", "discovery_requests", "pipeline_runs", "jobs", "quota_usage"] });

  if (error) {
    return (
      <div className="page">
        <PageHeader
          eyebrow={<QuietBreadcrumbs items={[{ label: "Workspace" }, { label: "Overview" }]} />}
          title="Your workspace"
          description="Opportunity intelligence · verified contacts · next-best actions"
        />
        <ErrorState error={error} onRetry={refresh} />
        <a href="/leads" className="link inline-flex items-center gap-1 text-sm">
          Go to Leads <Icon name="arrow" size={14} />
        </a>
      </div>
    );
  }
  if (loading || !data) {
    return (
      <div className="page" aria-busy="true" aria-live="polite">
        <div className="page-header">
          <div className="min-w-0 flex-1">
            <div className="skeleton h-3 w-32" aria-hidden="true" />
            <p className="page-title mt-1.5 text-ink-muted">Loading your workspace…</p>
            <p className="page-sub text-xs">
              If this stays more than a few seconds: confirm you created a portal login under Admin → Clients,
              and that this user is in tenant_members for the right tenant.
            </p>
          </div>
          <a href="/leads" className="btn-secondary">
            Go to Leads <Icon name="arrow" size={14} />
          </a>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-hidden="true">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="kpi">
              <div className="skeleton h-3 w-20" />
              <div className="skeleton mt-3 h-7 w-16" />
              <div className="skeleton mt-3 h-3 w-24" />
            </div>
          ))}
        </div>
        <div className="grid gap-4 lg:grid-cols-3" aria-hidden="true">
          <div className="skeleton h-56 rounded-xl lg:col-span-2" />
          <div className="skeleton h-56 rounded-xl" />
        </div>
      </div>
    );
  }

  const { kpis, funnel, usage, quota_limit, quota_reserved, quota_remaining, quota_used, tasks_due, recent_activity, tenant, trend } = data as any;
  const used = quota_used ?? usage?.leads_delivered ?? 0;
  const reserved = quota_reserved ?? usage?.quota_reserved ?? 0;
  const available = quota_remaining ?? Math.max(Number(quota_limit || 0) - Number(used) - Number(reserved), 0);
  const limit = quota_limit ?? 0;
  const quotaPct = limit ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  const sparkData = (trend || []).map((t: any) => ({ value: t.count }));

  const tasksDue: any[] = tasks_due || [];
  const activity: any[] = (recent_activity || []).slice(0, 8);
  const overdue = Number(intel?.tasks?.overdue ?? 0);
  const slaBreaches = Number(intel?.tasks?.sla_breaches ?? 0);
  const atRisk = Number(intel?.pipeline?.at_risk ?? 0);
  const missingNext = Number(intel?.pipeline?.missing_next_action ?? 0);

  const nextActions: Array<{ title: string; href: string; hint: string; icon: IconName; count?: number; tone: string }> = [
    { title: "Call Tier A", href: "/leads", hint: "High-priority leads, call first", icon: "phone", count: kpis?.tier_a ?? 0, tone: "badge-accent" },
    { title: "Request opportunities", href: "/search", hint: "Build the next approved discovery batch", icon: "search", tone: "badge-accent" },
    { title: "Review replies", href: "/replies", hint: "Turn responses into pipeline", icon: "reply", tone: "badge-brand" },
    { title: "Move deals", href: "/pipeline", hint: "Active opportunities in motion", icon: "pipeline", count: kpis?.in_pipeline ?? 0, tone: "badge-neutral" },
    { title: "Clear the SLA queue", href: "/activities", hint: `${slaBreaches.toLocaleString()} SLA breaches`, icon: "clock", count: overdue, tone: overdue ? "badge-warn" : "badge-neutral" },
  ];

  const steps = ["Open Leads → filter Tier A", "Read the pitch line", "Call or email", "Move stage in Pipeline"];

  return (
    <div className="page">
      <PageHeader
        eyebrow={<QuietBreadcrumbs items={[{ label: "Workspace" }, { label: "Overview" }]} />}
        title={tenant?.name ? tenant.name : "Your workspace"}
        description="Opportunity intelligence · verified contacts · next-best actions"
        actions={
          <>
            <Link href="/reports" className="btn-secondary">
              <Icon name="report" size={14} />
              Reports
            </Link>
            <Link href="/leads" className="btn-primary">
              Work leads
              <Icon name="arrow" size={14} />
            </Link>
          </>
        }
        meta={
          <>
            <span className="inline-flex items-center gap-1.5" role="status" aria-live="polite">
              <span className="live-dot" aria-hidden="true" />
              {refreshing ? "Syncing live…" : "Live workspace"}
            </span>
            {limit > 0 ? (
              <span className="tabular-nums">
                {Number(used).toLocaleString()} used · {Number(reserved).toLocaleString()} reserved · {Number(available).toLocaleString()} available of {Number(limit).toLocaleString()} monthly leads
              </span>
            ) : null}
          </>
        }
      />
      {pollingExpired && <PollingDeadlineState onRefresh={() => { setPollingExpired(false); refresh(); }} />}

      <StatGrid cols={4}>
        <StatCard
          label="Leads ready"
          value={Number(kpis?.total_leads ?? 0).toLocaleString()}
          tone="brand"
          href="/leads"
          icon={<Icon name="leads" size={16} />}
          hint={
            <span className="flex items-end justify-between gap-3">
              <span>Assigned to you</span>
              {sparkData.length > 0 ? <Sparkline data={sparkData} tone="brand" label="Lead delivery trend" className="h-7 w-20" /> : null}
            </span>
          }
        />
        <StatCard label="Tier A" value={Number(kpis?.tier_a ?? 0).toLocaleString()} hint="Best quality, call first" tone="accent" href="/leads" icon={<Icon name="spark" size={16} />} />
        <StatCard label="In pipeline" value={Number(kpis?.in_pipeline ?? 0).toLocaleString()} hint="Active work" tone="brand" href="/pipeline" icon={<Icon name="pipeline" size={16} />} />
        <StatCard label="Win rate" value={`${kpis?.win_rate_pct ?? 0}%`} hint={<span className="tabular-nums">{Number(kpis?.won ?? 0).toLocaleString()} won</span>} tone="success" href="/reports" icon={<Icon name="revenue" size={16} />} />
      </StatGrid>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel
          id="dashboard-revenue"
          className="lg:col-span-2"
          title="Revenue health"
          description="Pipeline value, weighted forecast and the deals that need attention."
          actions={<Link href="/pipeline" className="btn-ghost btn-sm">Open pipeline <Icon name="arrow" size={14} /></Link>}
        >
          <dl className="grid grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-4">
            <div>
              <dt className="stat-label">Pipeline</dt>
              <dd className="mt-1.5 font-display text-xl font-semibold tabular-nums text-ink">{money(intel?.pipeline?.pipeline_value)}</dd>
            </div>
            <div>
              <dt className="stat-label">Weighted</dt>
              <dd className="mt-1.5 font-display text-xl font-semibold tabular-nums text-ink">{money(intel?.pipeline?.weighted_value)}</dd>
            </div>
            <div>
              <dt className="stat-label">At risk</dt>
              <dd className={`mt-1.5 font-display text-xl font-semibold tabular-nums ${atRisk ? "text-rust-600 dark:text-rust-300" : "text-ink"}`}>{atRisk.toLocaleString()}</dd>
            </div>
            <div>
              <dt className="stat-label">No next action</dt>
              <dd className={`mt-1.5 font-display text-xl font-semibold tabular-nums ${missingNext ? "text-amber-700 dark:text-amber-300" : "text-ink"}`}>{missingNext.toLocaleString()}</dd>
            </div>
          </dl>
          <p className="mt-3 text-xs text-ink-faint">Pipeline is the sum of open CRM deal values. Weighted is each open deal value multiplied by its CRM probability; leads without a deal are not assigned a dollar value.</p>
          {limit > 0 && (
            <div className="mt-6 border-t border-line pt-4">
              <div className="flex items-center justify-between text-xs text-ink-muted">
                <span className="font-medium">Monthly quota</span>
                <span className="tabular-nums">
                  {Number(used).toLocaleString()} used · {Number(reserved).toLocaleString()} reserved · {Number(available).toLocaleString()} available / {Number(limit).toLocaleString()} · {quotaPct}% used
                </span>
              </div>
              <div
                className="mt-2 h-2 overflow-hidden rounded-full bg-surface-3"
                role="progressbar"
                aria-label="Monthly lead quota used"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={quotaPct}
              >
                <div className={`h-full rounded-full transition-all ${quotaPct >= 90 ? "bg-amber-500" : "bg-brand-600 dark:bg-brand-300"}`} style={{ width: `${quotaPct}%` }} />
              </div>
            </div>
          )}
        </Panel>

        <Panel id="dashboard-next-actions" title="Next actions" description="Where evidence is strongest right now." bodyClassName="p-0">
          <ul className="divide-y divide-line">
            {nextActions.map((action) => (
              <li key={action.title}>
                <Link href={action.href} className="group flex items-center gap-3 px-5 py-3 transition hover:bg-surface-3/60">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-line bg-surface-3 text-ink-muted group-hover:text-brand-700 dark:group-hover:text-brand-300">
                    <Icon name={action.icon} size={15} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-[0.8125rem] font-semibold text-ink">{action.title}</span>
                    <span className="block truncate text-xs text-ink-faint">{action.hint}</span>
                  </span>
                  {action.count != null ? <span className={`${action.tone} tabular-nums`}>{Number(action.count).toLocaleString()}</span> : null}
                  <Icon name="chevron" size={14} className="text-ink-faint" />
                </Link>
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel id="dashboard-funnel" className="lg:col-span-2" title="Pipeline funnel" description="Lead flow by stage, with conversion from the first stage.">
          <FunnelChart stages={(funnel || []).map((f: any) => ({ stage: f.stage || f.name || "Stage", count: Number(f.count) || 0, slug: f.slug }))} />
        </Panel>
        <Panel id="dashboard-tiers" title="Tier mix" description="Quality distribution of assigned leads.">
          <TierDonut a={kpis?.tier_a ?? 0} b={kpis?.tier_b ?? 0} c={kpis?.tier_c ?? 0} />
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel
          id="dashboard-tasks"
          title="Tasks due"
          actions={<Link href="/activities" className="btn-ghost btn-sm">Open activity queue <Icon name="arrow" size={14} /></Link>}
          bodyClassName="p-0"
        >
          {tasksDue.length === 0 ? (
            <EmptyState title="No open tasks" hint="Follow-ups you schedule on leads and deals appear here." icon={<Icon name="check" size={18} />} />
          ) : (
            <ul className="divide-y divide-line" aria-label="Tasks due">
              {tasksDue.map((t: any) => (
                <li key={t.id} className="flex items-center justify-between gap-3 px-5 py-3">
                  <span className="min-w-0 truncate text-[0.8125rem] font-medium text-ink">{t.title}</span>
                  <span className="shrink-0 text-xs tabular-nums text-ink-faint">{t.due_at ? String(t.due_at).slice(0, 10) : ""}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel id="dashboard-activity" title="Recent activity" bodyClassName="p-0">
          {activity.length === 0 ? (
            <EmptyState title="No activity yet" hint="Calls, emails and stage moves will build a timeline here." icon={<Icon name="activity" size={18} />} />
          ) : (
            <ol className="divide-y divide-line" aria-label="Recent activity">
              {activity.map((a: any) => (
                <li key={a.id} className="flex gap-3 px-5 py-3">
                  <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-brand-500 dark:bg-brand-300" aria-hidden="true" />
                  <div className="min-w-0">
                    <div className="text-[0.8125rem] font-medium text-ink">{a.title || a.activity_type}</div>
                    {a.body ? <div className="mt-0.5 line-clamp-2 text-xs text-ink-faint">{a.body}</div> : null}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </Panel>
      </div>

      <Panel id="dashboard-start" title="Start here" description="The four-step daily motion for new reps.">
        <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {steps.map((step, i) => (
            <li key={step} className="flex items-center gap-3 text-[0.8125rem] text-ink">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-brand-200 bg-brand-50 text-xs font-semibold tabular-nums text-brand-700 dark:border-brand-500/30 dark:bg-brand-500/15 dark:text-brand-200">
                {i + 1}
              </span>
              {step}
            </li>
          ))}
        </ol>
      </Panel>
    </div>
  );
}

function money(value: unknown) {
  return `$${Number(value || 0).toLocaleString()}`;
}
