"use client";

import { useLiveLoader } from "@/lib/live";
import { useCallback, useEffect, useState } from "react";
import { admin } from "@/lib/admin";
import { ErrorState, EmptyState, LoadingRows } from "@/components/ui/States";
import { Icon } from "@/components/ui/Icon";
import { PageHeader } from "@/components/ui/Primitives";

function percent(value: unknown) {
  if (value == null || value === "") return "—";
  const number = Number(value);
  return Number.isFinite(number) ? `${Math.round(number)}%` : String(value);
}

export default function AdminOpsPage() {
  const [ops, setOps] = useState<any>(null);
  const [health, setHealth] = useState<any>(null);
  const [keys, setKeys] = useState<any>(null);
  const [providers, setProviders] = useState<any>(null);
  const [days, setDays] = useState(14);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState("");
  const [pollingExpired, setPollingExpired] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const results = await Promise.allSettled([admin.ops(days), admin.health(), admin.keysHealth(), admin.providerHealth()]);
    const [opsResult, healthResult, keysResult, providersResult] = results;
    if (opsResult.status === "fulfilled") setOps(opsResult.value); else setError(opsResult.reason instanceof Error ? opsResult.reason : new Error(String(opsResult.reason)));
    if (healthResult.status === "fulfilled") setHealth(healthResult.value);
    if (keysResult.status === "fulfilled") setKeys(keysResult.value);
    if (providersResult.status === "fulfilled") setProviders(providersResult.value);
    setLoading(false);
  }, [days]);

  useLiveLoader(load, 30000, [days], { onDeadline: () => setPollingExpired(true) });

  async function refreshPriors(vertical: string) {
    setBusy(vertical);
    setMessage("");
    try { await admin.refreshPriors(vertical); setMessage(`Priors refreshed for ${vertical}.`); }
    catch (e: any) { setError(e instanceof Error ? e : new Error(String(e))); }
    finally { setBusy(""); }
  }

  const totals = ops?.totals || {};
  const providerRows = providers?.providers || providers?.items || providers?.checks || [];
  const metricCards = [
    ["Enrichment success", percent(ops?.enrichment_success_pct ?? ops?.enrich_success_pct ?? totals.enrichment_success_pct), "Evidence quality"],
    ["Enriched", totals.leads_enriched_ok ?? ops?.leads_enriched_ok ?? "—", `last ${days}d`],
    ["Enrichment failures", totals.leads_enriched_fail ?? ops?.leads_enriched_fail ?? "—", "Needs review"],
    ["Suppression matches", totals.suppressed ?? ops?.suppressed ?? "—", "Blocked before delivery"],
  ];

  return (
    <div className="page">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <PageHeader eyebrow="Control plane · reliability" title="Ops health" description="Monitor the evidence pipeline, provider lanes, key pool and delivery safety without hiding degraded dependencies." actions={<div className="flex items-center gap-2"><select aria-label="Ops history window" className="input w-auto" value={days} onChange={(e) => setDays(Number(e.target.value))}><option value={7}>7 days</option><option value={14}>14 days</option><option value={30}>30 days</option></select><button type="button" className="btn-secondary" onClick={() => void load()} disabled={loading}>Refresh</button></div>} />
      </header>

      {pollingExpired && <div role="status" className="rounded-xl border border-amber-300/60 bg-amber-50/60 px-4 py-3 text-sm text-amber-900 dark:bg-amber-500/10 dark:text-amber-200">Live updates paused. Refresh to start a new monitoring window.</div>}
      {message && <div role="status" className="rounded-xl border border-signal-300/60 bg-signal-50/70 px-4 py-3 text-sm text-signal-800 dark:bg-signal-500/10 dark:text-signal-200">{message}</div>}

      <section className={`rounded-2xl border p-4 ${health?.ok === false ? "border-rust-300/70 bg-rust-50/60 dark:bg-rust-500/10" : "border-signal-300/70 bg-signal-50/60 dark:bg-signal-500/10"}`}><div className="flex items-center gap-2"><span className={`live-dot ${health?.ok === false ? "bg-rust-500" : ""}`} /><span className="text-sm font-semibold text-ink">Platform {health?.ok === false ? "degraded" : "healthy"}</span></div><p className="mt-1 text-xs text-ink-muted">DB {String(health?.checks?.db ?? "—")} · version {health?.version || "—"}. A healthy API does not override individual provider or queue failures.</p></section>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{metricCards.map(([label, value, hint], index) => <div className="kpi" key={String(label)}><div className="flex items-center gap-2"><Icon name={index === 0 ? "brain" : index === 3 ? "suppression" : "health"} size={15} className="text-brand-600" /><span className="label">{label}</span></div><div className="kpi-value mt-2">{loading ? "…" : String(value)}</div><div className="mt-1 text-xs text-ink-faint">{hint}</div></div>)}</section>

      <section className="grid gap-5 lg:grid-cols-[1.1fr_.9fr]">
        <div className="data-card p-5"><div className="flex items-start justify-between gap-3"><div><div className="section-kicker">Provider lanes</div><h2 className="mt-1 text-lg font-semibold text-ink">Discovery and enrichment dependencies</h2></div><Icon name="integration" className="text-brand-600" /></div>{loading && !providers ? <LoadingRows rows={4} cols={3} /> : providerRows.length === 0 ? <EmptyState title="No provider details returned" hint="The API is available, but this environment did not expose provider-level telemetry." /> : <div className="mt-4 grid gap-2 sm:grid-cols-2">{providerRows.slice(0, 8).map((provider: any, index: number) => <div key={provider.id || provider.name || index} className="action-card"><div className="flex items-center justify-between gap-2"><span className="truncate text-sm font-semibold text-ink">{provider.name || provider.provider || provider.key || `Provider ${index + 1}`}</span><span className={`pill ${provider.ok === false || provider.status === "down" ? "bg-rust-50 text-rust-700 dark:bg-rust-900/30 dark:text-rust-300" : "bg-signal-50 text-signal-700 dark:bg-signal-500/15 dark:text-signal-300"}`}>{provider.ok === false || provider.status === "down" ? "Degraded" : "Ready"}</span></div><div className="mt-1 text-xs text-ink-faint">{provider.reason || provider.latency_ms ? `${provider.reason || "Latency"}${provider.latency_ms ? ` · ${provider.latency_ms}ms` : ""}` : "Telemetry available"}</div></div>)}</div>}</div>
        <div className="data-card p-5"><div className="flex items-start justify-between gap-3"><div><div className="section-kicker">Credential pool</div><h2 className="mt-1 text-lg font-semibold text-ink">Keys and cooldowns</h2></div><Icon name="key" className="text-brand-600" /></div><div className="mt-4 grid grid-cols-2 gap-3">{[["Healthy", keys?.healthy ?? keys?.ok ?? "—"], ["Cooldown", keys?.cooldown ?? keys?.cooling_down ?? "—"], ["Exhausted", keys?.exhausted ?? "—"], ["Total", keys?.total ?? "—"]].map(([label, value]) => <div className="action-card" key={String(label)}><div className="stat-label">{label}</div><div className="mt-1 text-xl font-semibold text-ink">{String(value)}</div></div>)}</div><a href="/admin/keys" className="mt-4 inline-flex text-xs font-semibold text-brand-600 hover:underline">Manage provider keys →</a></div>
      </section>

      <section className="data-card p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="section-kicker">Quality calibration</div><h2 className="mt-1 text-lg font-semibold text-ink">Refresh vertical priors</h2><p className="mt-1 text-sm text-ink-muted">Use recent outcomes to tune fit and opportunity scoring before the next target run.</p></div><Icon name="growth" className="text-signal-600" /></div><div className="mt-4 flex flex-wrap gap-2">{["medspa", "salon", "dental", "gym", "wellness"].map((vertical) => <button type="button" key={vertical} onClick={() => void refreshPriors(vertical)} disabled={!!busy} className="btn-secondary">{busy === vertical ? "Refreshing…" : vertical}</button>)}</div></section>
    </div>
  );
}
