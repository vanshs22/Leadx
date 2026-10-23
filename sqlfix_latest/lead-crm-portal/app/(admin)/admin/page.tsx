"use client";

import { useLiveLoader } from "@/lib/live";
import { useState } from "react";
import Link from "next/link";
import { admin, adminExtras } from "@/lib/admin";
import { chartColors } from "@/lib/chartColors";
import { ErrorState, PollingDeadlineState } from "@/components/ui/States";
import { Icon } from "@/components/ui/Icon";
import { PageHeader } from "@/components/ui/Primitives";
import dynamic from "next/dynamic";
const Sparkline = dynamic(() => import("@/components/charts/Sparkline").then((m) => m.Sparkline), { ssr: false, loading: () => <div className="skeleton h-12 w-full rounded-lg" /> });

export default function AdminOverviewPage() {
  const [tenants, setTenants] = useState<any[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [ops, setOps] = useState<any>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [system, setSystem] = useState<any>(null);
  const [queueStats, setQueueStats] = useState<any>(null);
  const [tenantHealth, setTenantHealth] = useState<any>(null);
  const [cron, setCron] = useState<any>(null);
  const [keyHealth, setKeyHealth] = useState<any>(null);
  const [providerHealth, setProviderHealth] = useState<any>(null);
  const [platformSummary, setPlatformSummary] = useState<any>(null);
  const [actionBusy, setActionBusy] = useState("");
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [pollingExpired, setPollingExpired] = useState(false);
  const [pollingReset, setPollingReset] = useState(0);

  const load = async () => {
    setErr("");
    const calls = await Promise.allSettled([
      admin.tenants(),
      admin.globalStats(),
      admin.ops(14),
      admin.health(),
      adminExtras.jobs({ limit: 1 }),
      admin.tenantHealth(),
      adminExtras.cronAudit(),
      admin.keysHealth(),
      admin.providerHealth(),
      admin.platformSummary(),
    ]);
    const [tenantsResult, statsResult, opsResult, healthResult, queueResult, tenantHealthResult, cronResult, keysResult, providersResult, summaryResult] = calls;
    if (tenantsResult.status === "fulfilled") { const d: any = tenantsResult.value; setTenants(d.tenants || d || []); }
    if (statsResult.status === "fulfilled") setStats(statsResult.value);
    if (opsResult.status === "fulfilled") setOps(opsResult.value);
    if (healthResult.status === "fulfilled") setSystem(healthResult.value); else setSystem({ ok: false });
    if (queueResult.status === "fulfilled") setQueueStats(queueResult.value.stats); else setQueueStats(null);
    if (tenantHealthResult.status === "fulfilled") setTenantHealth(tenantHealthResult.value); else setTenantHealth(null);
    if (cronResult.status === "fulfilled") setCron(cronResult.value); else setCron(null);
    if (keysResult.status === "fulfilled") setKeyHealth(keysResult.value); else setKeyHealth(null);
    if (providersResult.status === "fulfilled") setProviderHealth(providersResult.value); else setProviderHealth(null);
    if (summaryResult.status === "fulfilled") setPlatformSummary(summaryResult.value); else setPlatformSummary(null);
    if (calls.every((call) => call.status === "rejected")) setErr("The control plane is temporarily unavailable.");
    setLoading(false);
    setLastRefresh(new Date());
  };

  const refresh = () => {
    setPollingExpired(false);
    setPollingReset((value) => value + 1);
    void load();
  };

  useLiveLoader(load, 30000, [pollingReset], {
    onDeadline: () => setPollingExpired(true),
  });

  async function control(label: string, fn: () => Promise<any>) {
    setActionBusy(label);
    setErr("");
    try { await fn(); refresh(); } catch (e: any) { setErr(e?.message || `Failed: ${label}`); } finally { setActionBusy(""); }
  }

  const actions = [
    { href: "/admin/pipeline", title: "Run pipeline", desc: "Discover → enrich → deliver for a client ICP" },
    { href: "/admin/intelligence", title: "Intelligence", desc: "Sample packs, QA accuracy, territories, bad leads" },
    { href: "/admin/tenants", title: "Clients", desc: "Quotas, branding, delivery channels" },
    { href: "/admin/icps", title: "ICPs", desc: "Vertical + geo + offer category targets" },
    { href: "/admin/keys", title: "API keys", desc: "Serper / crawl pools & cooldowns" },
    { href: "/admin/ops", title: "Ops health", desc: "Enrichment success, suppression, failures" },
    { href: "/admin/security", title: "Security & audit", desc: "Review access events and platform controls" },
  ];

  const byDay = (ops?.by_day || []).slice().reverse(); // oldest → newest for the sparkline
  const enrichTrend = byDay.map((d: any) => ({ value: d.leads_enriched_ok || 0 }));
  const runsTrend = byDay.map((d: any) => ({ value: d.pipeline_runs || 0 }));
  const totals = ops?.totals || {};
  const enrichOk = totals.leads_enriched_ok || 0;
  const enrichFail = totals.leads_enriched_fail || 0;
  const enrichRate = enrichOk + enrichFail ? Math.round((100 * enrichOk) / (enrichOk + enrichFail)) : null;

  const queue = queueStats || system?.queue || {};
  const kpis = [
    { label: "Active clients", value: stats?.tenants_active ?? "—", hint: `${stats?.tenants_total ?? 0} total` },
    { label: "Leads in pool", value: stats?.leads_global ?? "—", hint: "Global inventory" },
    { label: "Delivered", value: stats?.assignments ?? "—", hint: "Total assignments" },
    { label: "Enrichment success", value: enrichRate != null ? `${enrichRate}%` : "—", hint: `last ${ops?.days ?? 14}d`, spark: enrichTrend, color: chartColors.signal[500] },
    { label: "Queue", value: queue.queued ?? "—", hint: `${queue.running ?? 0} running · ${queue.failed ?? 0} failed` },
  ];

  return (
    <div className="page">
      <PageHeader eyebrow="LeadX control plane" title="Operator console" description="Run the service, protect quality, and keep clients supplied." actions={<div className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-semibold ${system?.ok === false ? "border-rust-300 bg-rust-50 text-rust-700 dark:bg-rust-500/10 dark:text-rust-300" : "border-signal-300 bg-signal-50 text-signal-700 dark:bg-signal-500/10 dark:text-signal-300"}`}><span className="h-1.5 w-1.5 rounded-full bg-current" /> API {system?.ok === false ? "degraded" : "healthy"}</div>} />

      {err && <ErrorState error={err} onRetry={refresh} />}
      {pollingExpired && <PollingDeadlineState onRefresh={refresh} />}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        {kpis.map((k) => (
          <div key={k.label} className="kpi">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="label">{k.label}</div>
                <div className="kpi-value mt-2">{loading ? "…" : k.value}</div>
              </div>
              {k.spark && k.spark.length > 1 && <Sparkline data={k.spark} color={k.color} />}
            </div>
            <div className="mt-1 text-xs text-ink-faint">{k.hint}</div>
          </div>
        ))}
      </div>

      {byDay.length > 1 && (
        <div className="card-pad">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-ink">Pipeline runs — last {ops?.days ?? 14} days</h2>
            <Link href="/admin/runs" className="text-xs font-medium text-brand-600 hover:underline dark:text-brand-700 dark:text-brand-300">
              Run history
            </Link>
          </div>
          <div className="mt-4 h-16 w-full">
            <Sparkline data={runsTrend} color={chartColors.brand[500]} />
          </div>
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {actions.map((a) => (
          <Link key={a.href} href={a.href} className="card-pad transition hover:shadow-lift hover:border-brand-300 dark:hover:border-brand-500/40">
            <div className="flex items-center gap-2 text-base font-semibold text-ink"><Icon name={a.href.includes("security") ? "security" : a.href.includes("ops") ? "health" : a.href.includes("intelligence") ? "brain" : a.href.includes("clients") ? "clients" : a.href.includes("icps") ? "icp" : a.href.includes("keys") ? "key" : "run"} size={17} className="text-brand-600" />{a.title}</div>
            <p className="mt-2 text-sm text-ink-muted">{a.desc}</p>
          </Link>
        ))}
      </div>

      <section className="hero-panel overflow-hidden p-5 md:p-6">
        <div className="flex flex-col gap-5 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <div className="section-kicker text-signal-700 dark:text-signal-300">Control center</div>
            <h2 className="mt-1 text-xl font-semibold text-ink md:text-2xl">Operate the entire revenue engine</h2>
            <p className="mt-1 max-w-2xl text-sm text-ink-muted">Client health, discovery, enrichment, delivery, queues, billing and quality—one place to spot risk and act.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" aria-label="Run all ICPs" className="btn-primary" disabled={!!actionBusy} onClick={() => control("pipeline", () => admin.runAllIcps({}))}>{actionBusy === "pipeline" ? "Starting…" : "Run all ICPs"}</button>
            <button type="button" aria-label="Run operator alerts" className="btn-secondary" disabled={!!actionBusy} onClick={() => control("alerts", () => admin.runAlerts())}>{actionBusy === "alerts" ? "Running…" : "Run alerts"}</button>
            <button type="button" aria-label="Reap stuck jobs" className="btn-secondary" disabled={!!actionBusy} onClick={() => control("reap", () => adminExtras.reapJobs())}>{actionBusy === "reap" ? "Repairing…" : "Reap stuck jobs"}</button>
          </div>
        </div>
        <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          {[
            ["Clients at risk", tenantHealth?.at_risk ?? tenantHealth?.risk_count ?? 0, "/admin/health"],
            ["Cron health", cron ? (cron.all_healthy ? "100%" : "Review") : "—", "/admin/automation"],
            ["Key pool", keyHealth?.healthy ?? keyHealth?.ok ?? "—", "/admin/keys"],
            ["Queue failures", queue?.failed ?? 0, "/admin/jobs"],
            ["Global leads", stats?.leads_global ?? "—", "/admin/leads"],
          ].map(([label, value, href]) => (
            <Link key={String(label)} href={String(href)} className="glass-panel p-3 hover:border-signal-300/60">
              <div className="stat-label">{label}</div>
              <div className="mt-1 text-lg font-semibold text-ink">{loading ? "…" : String(value)}</div>
            </Link>
          ))}
        </div>
        <div className="mt-5 grid gap-3 md:grid-cols-2">
          <div className="rounded-xl border border-line bg-surface-3/40 p-3"><div className="flex items-center gap-2"><Icon name="health" size={15} className="text-signal-600" /><span className="stat-label">Provider health</span></div><div className="mt-2 text-sm font-semibold text-ink">{providerHealth ? `${providerHealth.healthy ?? providerHealth.ok ?? "—"} healthy lanes` : "Unavailable"}</div><div className="mt-1 text-xs text-ink-faint">Discovery, enrichment and delivery dependencies</div></div>
          <div className="rounded-xl border border-line bg-surface-3/40 p-3"><div className="flex items-center gap-2"><Icon name="usage" size={15} className="text-signal-600" /><span className="stat-label">Platform snapshot</span></div><div className="mt-2 text-sm font-semibold text-ink">{platformSummary ? `${platformSummary.status || platformSummary.ok ? "Online" : "Review"}` : "Unavailable"}</div><div className="mt-1 text-xs text-ink-faint">Cost and platform telemetry are available in Usage</div></div>
        </div>
        {lastRefresh && <div className="mt-3 text-[10px] text-ink-faint">Auto-refresh every 30s · last checked {lastRefresh.toLocaleTimeString()}</div>}
      </section>

      <div className="grid gap-4 lg:grid-cols-3">
        <Link href="/admin/prospecting" className="action-card">
          <div className="stat-label">Acquisition</div><div className="mt-1 font-semibold text-ink">Prospecting engine</div><p className="mt-1 text-xs text-ink-muted">Discover new supply, seed markets and control source lanes.</p>
        </Link>
        <Link href="/admin/intelligence" className="action-card">
          <div className="stat-label">Quality moat</div><div className="mt-1 font-semibold text-ink">Intelligence & QA</div><p className="mt-1 text-xs text-ink-muted">Accuracy, bad leads, territories and outcome signals.</p>
        </Link>
        <Link href="/admin/usage" className="action-card">
          <div className="stat-label">Unit economics</div><div className="mt-1 font-semibold text-ink">Usage & billing</div><p className="mt-1 text-xs text-ink-muted">Credits, consumption, delivery economics and client limits.</p>
        </Link>
      </div>

      <div className="card-pad">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-ink">Clients</h2>
          <Link href="/admin/tenants" className="text-xs font-medium text-brand-600 hover:underline dark:text-brand-700 dark:text-brand-300">Manage all</Link>
        </div>
        <ul className="mt-3 divide-y divide-line">
          {tenants.slice(0, 12).map((t) => (
            <li key={t.id} className="flex items-center justify-between py-2.5 text-sm">
              <Link href={`/admin/tenants/${t.id}`} className="font-medium text-ink hover:text-brand-600 dark:hover:text-brand-700 dark:text-brand-300">{t.name || t.id}</Link>
              <span className="pill bg-surface-3 text-ink-muted">{t.status || "active"}</span>
            </li>
          ))}
          {!loading && !tenants.length && <li className="py-2 text-sm text-ink-faint">No tenants yet — create one under Clients</li>}
        </ul>
      </div>
    </div>
  );
}
