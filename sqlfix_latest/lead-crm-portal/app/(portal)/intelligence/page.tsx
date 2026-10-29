"use client";

import { useCallback, useMemo, useState } from "react";
import { crmFabric } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { useLiveLoader } from "@/lib/live";
import { DataState, EmptyState, LoadingRows, PollingDeadlineState } from "@/components/ui/States";
import { Badge, PageHeader, Panel, StatCard, StatGrid, StatusBadge } from "@/components/ui/Primitives";
import { Icon } from "@/components/ui/Icon";
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

  const fmt = (value: unknown) => (value == null ? "—" : typeof value === "number" ? value.toLocaleString() : String(value));

  return <div className="page">
    <PageHeader
      eyebrow="LeadX intelligence fabric"
      title="Discovery & intelligence"
      description="Plan coverage from the existing catalog, inspect executable lanes, and keep planner output separate from persisted CRM leads."
      actions={<button type="button" aria-label="Refresh intelligence fabric" className="btn-secondary" onClick={refresh}><Icon name="refresh" size={14} />Refresh</button>}
      meta={<FreshnessNote label="Read-side snapshot" detail={`${executable.length} executable adapters · no provider work on refresh`} />}
    />
    {deadline ? <PollingDeadlineState onRefresh={refresh} /> : null}
    <DataState
      loading={!data && !error}
      error={error}
      onRetry={refresh}
      isEmpty={!data && !error}
      empty={<EmptyState title="Intelligence fabric unavailable" hint="No fabric snapshot was returned for this workspace." />}
      skeleton={<div className="space-y-6" aria-busy="true" aria-live="polite"><span className="sr-only">Loading catalog and execution posture…</span><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5" aria-hidden="true">{[0, 1, 2, 3, 4].map((i) => <div key={i} className="kpi"><div className="skeleton h-3 w-24" /><div className="skeleton mt-3 h-7 w-12" /></div>)}</div><div className="grid gap-4 lg:grid-cols-5" aria-hidden="true"><div className="panel p-5 lg:col-span-2"><div className="skeleton h-4 w-40" /><div className="skeleton mt-4 h-24" /></div><div className="panel lg:col-span-3"><LoadingRows rows={6} cols={4} /></div></div></div>}
    >
      <StatGrid cols={5}>
        <StatCard label="Discovery catalog" value={fmt(overview.discovery_sources)} hint="Source inventory" icon={<Icon name="search" size={16} />} />
        <StatCard label="Enrichment catalog" value={fmt(overview.enrichment_catalog)} hint="Provider definitions" icon={<Icon name="integration" size={16} />} />
        <StatCard label="Executable adapters" value={fmt(overview.executable_enrichment_adapters)} hint="Configured runtime" tone="success" icon={<Icon name="run" size={16} />} />
        <StatCard label="Providers configured" value={fmt(configured.length)} hint="Native or endpoint" tone="brand" icon={<Icon name="key" size={16} />} />
        <StatCard label="Agents" value={fmt(overview.agents)} hint="Registered contracts" icon={<Icon name="brain" size={16} />} />
      </StatGrid>

      <div className="grid gap-4 lg:grid-cols-5">
        <Panel
          id="fabric-planner"
          className="lg:col-span-2"
          title="Plan coverage before execution"
          description="The planner normalizes vertical, locations, query families, surfaces, and bounded rounds. It does not search providers, create a discovery request, or write leads."
          actions={<Badge tone="neutral">Planner only</Badge>}
        >
          <div className="space-y-4" role="search" aria-label="Coverage planner inputs">
            <label className="block"><span className="field-label">Vertical</span><input aria-label="Planner vertical" className="input" value={vertical} onChange={(e) => setVertical(e.target.value)} /></label>
            <label className="block"><span className="field-label">Locations</span><input aria-label="Planner locations" className="input" value={locations} onChange={(e) => setLocations(e.target.value)} /><span className="field-hint block">Comma-separated.</span></label>
            <label className="block"><span className="field-label">Query families</span><input aria-label="Planner query families" className="input" value={queries} onChange={(e) => setQueries(e.target.value)} /><span className="field-hint block">Comma-separated search phrases.</span></label>
          </div>
          <div className="mt-5 flex flex-wrap items-center justify-end gap-2 border-t border-line pt-4">
            <button type="button" className="btn-ghost" onClick={() => { setVertical(""); setLocations(""); setQueries(""); }}>Clear planner inputs</button>
            <button type="button" aria-label="Generate planner coverage plan" disabled={busy || !tid || !vertical.trim()} onClick={createPlan} className="btn-primary">{busy ? "Planning…" : "Generate coverage plan"}</button>
          </div>
          {plan ? <div className="mt-4 rounded-lg border border-brand-200 bg-brand-50/60 p-4 dark:border-brand-500/30 dark:bg-brand-500/10">
            <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="section-title">Plan preview</h3><Badge tone="neutral">Not persisted</Badge></div>
            <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 text-[0.8125rem]">
              <div><dt className="label">Vertical</dt><dd className="mt-0.5 text-ink">{plan.vertical || "—"}</dd></div>
              <div><dt className="label">Max rounds</dt><dd className="mt-0.5 tabular-nums text-ink">{plan.max_rounds ?? "—"}</dd></div>
              <div><dt className="label">Locations</dt><dd className="mt-0.5 text-ink">{plan.locations?.join(", ") || "none supplied"}</dd></div>
              <div><dt className="label">Queries</dt><dd className="mt-0.5 tabular-nums text-ink">{plan.queries?.length ?? 0}</dd></div>
            </dl>
            <p className="mt-3 text-xs tabular-nums text-ink-muted">Persisted leads: 0 · provider calls: 0 · approval gate: not entered</p>
          </div> : null}
        </Panel>

        <Panel
          id="fabric-providers"
          className="lg:col-span-3"
          title="Configured vs executable providers"
          description="Catalog presence is not a credential or adapter. Only executable rows can participate in an explicit enrichment/research operation."
          actions={<Badge tone="success" className="tabular-nums">{executable.length} executable</Badge>}
          bodyClassName="p-0"
        >
          {providers.length ? <div className="max-h-[520px] overflow-auto">
            <table className="data-table">
              <caption className="sr-only">Enrichment providers and execution posture</caption>
              <thead><tr><th scope="col">Provider</th><th scope="col">Adapter</th><th scope="col">Credential</th><th scope="col">Status</th></tr></thead>
              <tbody>{providers.map((provider) => <tr key={provider.name}>
                <td className="max-w-[240px]"><div className="truncate font-medium text-ink">{provider.name}</div><div className="truncate text-xs text-ink-faint">{(provider.capabilities || []).join(", ") || "capabilities unavailable"}</div></td>
                <td className="text-ink-muted">{provider.native_adapter ? "Native adapter" : provider.endpoint_adapter ? "Configured endpoint adapter" : "No runtime adapter"}</td>
                <td className="text-ink-muted">{provider.configured ? "credential configured" : "credential unavailable"}</td>
                <td><StatusBadge status={provider.executable ? "active" : provider.configured ? "partial" : "unknown"} label={provider.executable ? "Executable" : provider.configured ? "Configured, not executable" : "Catalog only"} /></td>
              </tr>)}</tbody>
            </table>
          </div> : <div className="p-5"><UnavailableNote label="Provider catalog">The API returned no provider rows.</UnavailableNote></div>}
        </Panel>
      </div>

      <Panel
        id="fabric-sources"
        title="Free/open coverage vs paid or configured lanes"
        description="These are catalog lanes only. Execution still requires an explicit approved search or provider operation."
        actions={<><Badge tone="success" className="tabular-nums">Free/open {freeSources.length}</Badge><Badge tone="brand" className="tabular-nums">Paid/configured {paidSources.length}</Badge></>}
      >
        {data?.sources?.length ? <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4" aria-label="Discovery source lanes">{(data?.sources || []).slice(0, 24).map((source) => <li key={`${source.name}-${source.domain || ""}`} className="flex min-w-0 items-start gap-2.5 rounded-lg border border-line bg-surface-2 px-3 py-2.5">
          <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${source.mode === "web_domain" ? "bg-emerald-600 dark:bg-emerald-400" : "bg-brand-600 dark:bg-brand-300"}`} aria-hidden="true" />
          <div className="min-w-0"><div className="truncate text-[0.8125rem] font-medium text-ink">{source.name}</div><div className="mt-0.5 truncate text-xs text-ink-faint">{laneLabel(source)} · {source.category || "source"}</div></div>
        </li>)}</ul> : <EmptyState title="No discovery lanes returned" hint="The backend did not publish a source catalog for this workspace." />}
      </Panel>
    </DataState>
    {message ? <div role="status" aria-live="polite" className="notice notice-neutral">{message}</div> : null}
  </div>;
}
