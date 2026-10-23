"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { crm, crmIntelligence, DashboardResponse } from "@/lib/api";
import { getTenantId, getTenantUserId } from "@/lib/tenant";
import { FunnelChart } from "@/components/charts/FunnelChart";
import { chartColors } from "@/lib/chartColors";
import { useCrmChange } from "@/lib/crmRealtime";
import { useLiveLoader } from "@/lib/live";
import { ErrorState, PollingDeadlineState } from "@/components/ui/States";
import { PageHeader, QuietBreadcrumbs } from "@/components/ui/Primitives";
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
      <div className="page space-y-3">
        <ErrorState error={error} onRetry={refresh} />
        <a href="/leads" className="text-sm text-brand-500 hover:underline">
          Go to Leads →
        </a>
      </div>
    );
  }
  if (loading || !data) {
    return (
      <div className="page space-y-3 text-ink-faint">
        <p>Loading your workspace…</p>
        <p className="text-xs">
          If this stays more than a few seconds: confirm you created a portal login under Admin → Clients,
          and that this user is in tenant_members for the right tenant.
        </p>
        <a href="/leads" className="text-sm text-brand-500 hover:underline">
          Go to Leads →
        </a>
      </div>
    );
  }

  const { kpis, funnel, usage, quota_limit, tasks_due, recent_activity, tenant, trend } = data as any;
  const used = usage?.leads_delivered ?? 0;
  const limit = quota_limit ?? 0;
  const quotaPct = limit ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  const sparkData = (trend || []).map((t: any) => ({ value: t.count }));

  const cards = [
    { label: "Leads ready", value: kpis?.total_leads ?? 0, hint: "Assigned to you", href: "/leads", spark: sparkData, color: chartColors.brand[500] },
    { label: "Best quality", value: kpis?.tier_a ?? 0, hint: "Tier A — call first", href: "/leads", color: chartColors.brand[500] },
    { label: "In pipeline", value: kpis?.in_pipeline ?? 0, hint: "Active work", href: "/pipeline", color: chartColors.signal[500] },
    { label: "Win rate", value: `${kpis?.win_rate_pct ?? 0}%`, hint: `${kpis?.won ?? 0} won`, href: "/reports", color: chartColors.signal[500] },
  ];

  return (
    <div className="page">
      <PageHeader
        eyebrow={<QuietBreadcrumbs items={[{ label: "Workspace" }, { label: "Overview" }]} />}
        title={tenant?.name ? tenant.name : "Your workspace"}
        description="Opportunity intelligence · verified contacts · next-best actions"
        actions={<><div className="flex items-center gap-3">{refreshing && <span className="text-[11px] font-medium text-brand-500">Syncing live…</span>}<Link href="/leads" className="btn-primary">Work leads →</Link><Link href="/reports" className="btn-secondary">Reports</Link></div></>}
      />
      {pollingExpired && <PollingDeadlineState onRefresh={() => { setPollingExpired(false); refresh(); }} />}

      <div className="growth-banner overflow-hidden p-5 md:p-6">
          <div className="label text-brand-600 dark:text-brand-300">Start here — 4 steps</div>
          <ol className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[
              "Open Leads → filter Tier A",
              "Read the pitch line",
              "Call or email",
              "Move stage in Pipeline",
            ].map((step, i) => (
              <li key={step} className="flex gap-2 text-sm text-ink">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-500/15 text-xs font-bold text-brand-600 dark:text-brand-300">
                  {i + 1}
                </span>
                {step}
              </li>
            ))}
          </ol>
        </div>

      <section className="mt-6 grid gap-4 lg:grid-cols-[1.35fr_.65fr]">
        <div className="hero-panel p-5 md:p-6">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="section-kicker text-brand-700 dark:text-brand-300">Today’s opportunity brief</div>
              <h2 className="mt-1 text-lg font-semibold text-ink">Work the highest-signal opportunities first</h2>
              <p className="mt-1 text-sm text-ink-muted">LeadX combines fit, opportunity, intent and contactability so your reps spend time where evidence is strongest.</p>
            </div>
            <Link href="/leads" className="btn-secondary shrink-0">Open queue</Link>
          </div>
          <div className="mt-5 grid gap-3 sm:grid-cols-3">
            {[
              ["Priority", kpis?.tier_a ?? 0, "high-confidence leads"],
              ["Pipeline", kpis?.in_pipeline ?? 0, "active opportunities"],
              ["Win rate", `${kpis?.win_rate_pct ?? 0}%`, "historical close rate"],
            ].map(([a,b,c]) => <div key={String(a)} className="glass-panel p-3"><div className="stat-label">{a}</div><div className="mt-1 text-xl font-semibold text-ink">{b}</div><div className="text-[11px] text-ink-faint">{c}</div></div>)}
          </div>
        </div>
        <div className="data-card p-5">
          <div className="section-kicker">Next actions</div>
          <div className="mt-3 space-y-2">
            {[
              ["Call Tier A", `/leads`, `${kpis?.tier_a ?? 0} high-priority leads`],
              ["Review replies", `/replies`, "Turn responses into pipeline"],
              ["Move deals", `/pipeline`, `${kpis?.in_pipeline ?? 0} active opportunities`],
            ].map(([title, href, hint]) => <Link key={String(title)} href={String(href)} className="group flex items-center justify-between rounded-xl border border-line/70 bg-surface-3/40 p-3 hover:border-brand-300/60"><div><div className="text-sm font-semibold text-ink">{title}</div><div className="text-[11px] text-ink-faint">{hint}</div></div><span className="text-ink-faint transition group-hover:translate-x-0.5">→</span></Link>)}
          </div>
        </div>
      </section>

      <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {cards.map((c) => (
          <Link key={c.label} href={c.href} className="card-pad hover:border-brand-300/50 transition">
            <div className="text-xs font-medium text-ink-faint">{c.label}</div>
            <div className="mt-1 text-2xl font-semibold text-ink">{c.value}</div>
            <div className="mt-0.5 text-xs text-ink-muted">{c.hint}</div>
            {c.spark && c.spark.length > 0 ? (
              <div className="mt-3">
                <Sparkline data={c.spark} color={c.color} />
              </div>
            ) : null}
          </Link>
        ))}
      </div>

      {limit > 0 && (
        <div className="mt-4 card-pad">
          <div className="flex justify-between text-xs text-ink-muted">
            <span>Monthly quota</span>
            <span>
              {used} / {limit}
            </span>
          </div>
          <div className="mt-2 h-2 rounded-full bg-surface-3 overflow-hidden">
            <div className="h-full rounded-full bg-brand-500 transition-all" style={{ width: `${quotaPct}%` }} />
          </div>
        </div>
      )}

      <section className="mt-6 grid gap-4 lg:grid-cols-[1.2fr_.8fr]">
        <div className="data-card p-5">
          <div className="section-kicker">Revenue health</div>
          <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              ["Pipeline", `$${Number(intel?.pipeline?.pipeline_value || 0).toLocaleString()}`],
              ["Weighted", `$${Number(intel?.pipeline?.weighted_value || 0).toLocaleString()}`],
              ["At risk", intel?.pipeline?.at_risk ?? 0],
              ["No next action", intel?.pipeline?.missing_next_action ?? 0],
            ].map(([a,b]) => <div key={String(a)} className="action-card"><div className="stat-label">{a}</div><div className="mt-1 text-lg font-semibold text-ink">{b}</div></div>)}
          </div>
        </div>
        <div className="data-card p-5">
          <div className="section-kicker">SLA queue</div>
          <div className="mt-2 text-2xl font-semibold text-ink">{intel?.tasks?.overdue ?? 0}</div>
          <div className="text-xs text-ink-faint">overdue tasks · {intel?.tasks?.sla_breaches ?? 0} SLA breaches</div>
          <Link href="/activities" className="mt-3 inline-block text-xs font-semibold text-brand-600 hover:underline">Open activity queue →</Link>
        </div>
      </section>

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <div className="card-pad">
          <div className="label mb-3">Pipeline funnel</div>
          <FunnelChart stages={(funnel || []).map((f: any) => ({ stage: f.stage || f.name || "Stage", count: Number(f.count) || 0, slug: f.slug }))} />
        </div>
        <div className="card-pad">
          <div className="label mb-3">Tier mix</div>
          <TierDonut a={kpis?.tier_a ?? 0} b={kpis?.tier_b ?? 0} c={kpis?.tier_c ?? 0} />
        </div>
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <div className="card-pad">
          <div className="label mb-2">Tasks due</div>
          <ul className="space-y-2 text-sm">
            {(tasks_due || []).length === 0 && <li className="text-ink-faint">No open tasks</li>}
            {(tasks_due || []).map((t: any) => (
              <li key={t.id} className="flex justify-between gap-2 border-b border-line/60 py-1.5">
                <span className="text-ink">{t.title}</span>
                <span className="text-xs text-ink-faint shrink-0">{t.due_at ? String(t.due_at).slice(0, 10) : ""}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="card-pad">
          <div className="label mb-2">Recent activity</div>
          <ul className="space-y-2 text-sm">
            {(recent_activity || []).length === 0 && <li className="text-ink-faint">No activity yet</li>}
            {(recent_activity || []).slice(0, 8).map((a: any) => (
              <li key={a.id} className="border-b border-line/60 py-1.5">
                <div className="font-medium text-ink">{a.title || a.activity_type}</div>
                {a.body ? <div className="text-xs text-ink-faint line-clamp-2">{a.body}</div> : null}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
