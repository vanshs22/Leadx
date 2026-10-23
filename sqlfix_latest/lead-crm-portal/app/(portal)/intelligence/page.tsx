"use client";

import { useCallback, useMemo, useState } from "react";
import { crmFabric } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { useLiveLoader } from "@/lib/live";
import { DataState, EmptyState, PollingDeadlineState } from "@/components/ui/States";
import { PageHeader, FilterBar } from "@/components/ui/Primitives";
import { FreshnessNote, UnavailableNote } from "@/components/ui/SurfaceMeta";

type Provider = { name: string; mode?: string; configured?: boolean; executable?: boolean; native_adapter?: boolean; endpoint_adapter?: boolean; capabilities?: string[] };
type DiscoverySource = { name: string; mode?: string; category?: string; domain?: string; configured?: boolean };
type FabricData = { overview: any; providers: Provider[]; sources: DiscoverySource[] };
type Plan = { vertical?: string; locations?: string[]; queries?: string[]; surfaces?: string[]; max_rounds?: number; stop_new_rate?: number };

function laneLabel(source: DiscoverySource) {
  if (source.mode === "web_domain") return "Free/open web lane";
  if (source.mode === "connector") return "Configured connector lane";
  return "Paid/native lane";
}

export default function IntelligenceFabricPage() {
  const tid = getTenantId();
  const [data, setData] = useState<FabricData | null>(null);
  const [vertical, setVertical] = useState("medspa");
  const [locations, setLocations] = useState("Austin, Texas");
  const [queries, setQueries] = useState("med spa, medical spa, aesthetics clinic");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [deadline, setDeadline] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  const load = useCallback(async () => {
    if (!tid) return;
    try {
      const [overview, providers, discovery] = await Promise.all([
        crmFabric.overview(tid),
        crmFabric.providers(tid),
        crmFabric.discoverySources(tid),
      ]);
      setData({ overview, providers: providers.providers || [], sources: discovery.sources || [] });
      setError(null);
    } catch (caught) { setError(caught); }
  }, [tid]);
  useLiveLoader(load, 30_000, [refreshKey], { onDeadline: () => setDeadline(true) });
  useCrmChange(() => { void load(); }, { tables: ["discovery_requests", "pipeline_runs", "jobs", "agent_runs", "learning_recommendations"] });

  async function createPlan() {
    if (!tid || !vertical.trim()) return;
    setBusy(true); setMessage("");
    try {
      const result = await crmFabric.discoveryPlan(tid, {
        vertical: vertical.trim(),
        locations: locations.split(",").map((x) => x.trim()).filter(Boolean),
        queries: queries.split(",").map((x) => x.trim()).filter(Boolean),
        max_rounds: 8,
      });
      setPlan(result.plan || null);
      setMessage("Planner result ready. This is a plan-only operation; no provider was called and no lead was persisted.");
    } catch (caught: any) { setMessage(caught?.message || "Planner unavailable"); }
    finally { setBusy(false); }
  }

  const providers = data?.providers || [];
  const executable = useMemo(() => providers.filter((p) => p.executable), [providers]);
  const configured = useMemo(() => providers.filter((p) => p.configured || p.endpoint_adapter || p.native_adapter), [providers]);
  const freeSources = useMemo(() => (data?.sources || []).filter((s) => s.mode === "web_domain"), [data]);
  const paidSources = useMemo(() => (data?.sources || []).filter((s) => s.mode !== "web_domain"), [data]);
  const refresh = () => { setDeadline(false); setRefreshKey((value) => value + 1); };
  const overview = data?.overview?.fabric || {};

  return <div className="page space-y-5">
    <PageHeader eyebrow="LeadX intelligence fabric" title="Discovery & intelligence" description="Plan coverage from the existing catalog, inspect executable lanes, and keep planner output separate from persisted CRM leads." actions={<button type="button" aria-label="Refresh intelligence fabric" className="btn-secondary" onClick={refresh}>Refresh</button>} meta={<FreshnessNote label="Read-side snapshot" detail={`${executable.length} executable adapters · no provider work on refresh`} />} />
    {deadline ? <PollingDeadlineState onRefresh={refresh} /> : null}
    <DataState loading={!data && !error} error={error} onRetry={refresh} isEmpty={!data && !error} empty={<EmptyState title="Intelligence fabric unavailable" hint="No fabric snapshot was returned for this workspace." />} skeleton={<div className="card-pad" aria-busy="true">Loading catalog and execution posture…</div>}>
      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {[['Discovery catalog', overview.discovery_sources, 'inventory'], ['Enrichment catalog', overview.enrichment_catalog, 'definitions'], ['Executable adapters', overview.executable_enrichment_adapters, 'configured runtime'], ['Providers configured', configured.length, 'native or endpoint'], ['Agents', overview.agents, 'registered contracts']].map(([label, value, sub]) => <div key={String(label)} className="card-pad"><div className="text-xs text-ink-faint">{label}</div><div className="mt-1 text-2xl font-semibold text-ink">{value ?? '—'}</div><div className="mt-1 text-[10px] uppercase tracking-wide text-ink-faint">{sub}</div></div>)}
      </section>

      <section className="grid gap-5 lg:grid-cols-[1fr_1.2fr]">
        <div className="card-pad">
          <div className="eyebrow">Planner-only lane</div><h2 className="mt-1 text-lg font-semibold text-ink">Plan coverage before execution</h2>
          <p className="mt-2 text-xs leading-5 text-ink-muted">The planner normalizes vertical, locations, query families, surfaces, and bounded rounds. It does not search providers, create a discovery request, or write leads.</p>
          <FilterBar className="mt-4" onClear={() => { setVertical(""); setLocations(""); setQueries(""); }} clearLabel="Clear planner inputs"><label className="block min-w-[150px] flex-1 text-xs font-medium text-ink-faint">Vertical<input aria-label="Planner vertical" className="input mt-1" value={vertical} onChange={(e) => setVertical(e.target.value)} /></label><label className="block min-w-[180px] flex-1 text-xs font-medium text-ink-faint">Locations<input aria-label="Planner locations" className="input mt-1" value={locations} onChange={(e) => setLocations(e.target.value)} /></label><label className="block min-w-[220px] flex-[2] text-xs font-medium text-ink-faint">Query families<input aria-label="Planner query families" className="input mt-1" value={queries} onChange={(e) => setQueries(e.target.value)} /></label></FilterBar>
          <button type="button" aria-label="Generate planner coverage plan" disabled={busy || !tid || !vertical.trim()} onClick={createPlan} className="btn-primary mt-3 w-full">{busy ? "Planning…" : "Generate coverage plan"}</button>
          {plan ? <div className="mt-4 rounded-xl border border-brand-200 bg-brand-50/60 p-3 text-sm dark:border-brand-500/30 dark:bg-brand-500/10"><div className="flex flex-wrap items-center justify-between gap-2"><strong>Plan preview</strong><span className="pill bg-surface-2 text-ink-muted">Not persisted</span></div><dl className="mt-3 grid grid-cols-2 gap-2 text-xs"><div><dt className="text-ink-faint">Vertical</dt><dd>{plan.vertical || '—'}</dd></div><div><dt className="text-ink-faint">Max rounds</dt><dd>{plan.max_rounds ?? '—'}</dd></div><div><dt className="text-ink-faint">Locations</dt><dd>{plan.locations?.join(', ') || 'none supplied'}</dd></div><div><dt className="text-ink-faint">Queries</dt><dd>{plan.queries?.length ?? 0}</dd></div></dl><p className="mt-3 text-[11px] text-ink-muted">Persisted leads: 0 · provider calls: 0 · approval gate: not entered</p></div> : null}
        </div>

        <div className="card-pad overflow-hidden"><div className="eyebrow">Execution truth</div><h2 className="mt-1 text-lg font-semibold text-ink">Configured vs executable providers</h2><p className="mt-1 text-xs text-ink-muted">Catalog presence is not a credential or adapter. Only executable rows can participate in an explicit enrichment/research operation.</p><div className="mt-4 max-h-[500px] overflow-auto divide-y divide-line">{providers.map((provider) => <div key={provider.name} className="py-3"><div className="flex items-center justify-between gap-3"><span className="font-medium text-ink">{provider.name}</span><span className={`pill ${provider.executable ? 'bg-signal-50 text-signal-700 dark:bg-signal-500/15 dark:text-signal-300' : 'bg-surface-3 text-ink-faint'}`}>{provider.executable ? 'Executable' : provider.configured ? 'Configured, not executable' : 'Catalog only'}</span></div><div className="mt-1 text-[10px] text-ink-faint">{provider.native_adapter ? 'Native adapter' : provider.endpoint_adapter ? 'Configured endpoint adapter' : 'No runtime adapter'} · {provider.configured ? 'credential configured' : 'credential unavailable'} · {(provider.capabilities || []).join(', ') || 'capabilities unavailable'}</div></div>)}{!providers.length ? <UnavailableNote label="Provider catalog">The API returned no provider rows.</UnavailableNote> : null}</div></div>
      </section>

      <section className="card-pad"><div className="flex flex-wrap items-end justify-between gap-3"><div><div className="eyebrow">Discovery source lanes</div><h2 className="mt-1 text-lg font-semibold text-ink">Free/open coverage vs paid or configured lanes</h2><p className="mt-1 text-xs text-ink-muted">These are catalog lanes only. Execution still requires an explicit approved search or provider operation.</p></div><div className="flex gap-2 text-xs"><span className="pill bg-signal-50 text-signal-700 dark:bg-signal-500/15 dark:text-signal-300">Free/open {freeSources.length}</span><span className="pill bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-300">Paid/configured {paidSources.length}</span></div></div><div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">{(data?.sources || []).slice(0, 24).map((source) => <div key={`${source.name}-${source.domain || ''}`} className="rounded-lg border border-line p-3"><div className="text-sm font-medium text-ink">{source.name}</div><div className="mt-1 text-[11px] text-ink-faint">{laneLabel(source)} · {source.category || 'source'}</div></div>)}</div>{!data?.sources?.length ? <EmptyState title="No discovery lanes returned" hint="The backend did not publish a source catalog for this workspace." /> : null}</section>
    </DataState>
    {message ? <div role="status" aria-live="polite" className="rounded-xl border border-line bg-surface-2 px-4 py-3 text-sm text-ink-muted">{message}</div> : null}
  </div>;
}
