"use client";

import { useEffect, useMemo, useState } from "react";
import { crmFabric } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";

export default function IntelligenceFabricPage() {
  const tid = getTenantId();
  const [overview, setOverview] = useState<any>(null);
  const [providers, setProviders] = useState<any[]>([]);
  const [vertical, setVertical] = useState("medspa");
  const [locations, setLocations] = useState("Austin, Texas");
  const [queries, setQueries] = useState("med spa, medical spa, aesthetics clinic");
  const [plan, setPlan] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function load() {
    if (!tid) return;
    try {
      const [o, p] = await Promise.all([crmFabric.overview(tid), crmFabric.providers(tid)]);
      setOverview(o); setProviders(p.providers || []);
    } catch (e: any) { setMessage(e?.message || "Could not load intelligence fabric"); }
  }
  useEffect(() => { void load(); }, [tid]);
  useCrmChange(() => { void load(); }, { tables: ["discovery_requests", "pipeline_runs", "jobs", "agent_runs", "learning_recommendations"] });

  async function createPlan() {
    if (!tid) return;
    setBusy(true); setMessage("");
    try {
      const r = await crmFabric.discoveryPlan(tid, {
        vertical, locations: locations.split(",").map(x => x.trim()).filter(Boolean),
        queries: queries.split(",").map(x => x.trim()).filter(Boolean), max_rounds: 8,
      });
      setPlan(r.plan); setMessage("Discovery plan generated from the 14.0 planner. No leads were written.");
    } catch (e: any) { setMessage(e?.message || "Planner failed"); }
    finally { setBusy(false); }
  }

  const executable = useMemo(() => providers.filter(p => p.executable), [providers]);
  return <div className="page space-y-5">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div><div className="label text-brand-600 dark:text-brand-300">LeadX 14.0</div><h1 className="page-title mt-1">Data & Intelligence Fabric</h1><p className="page-sub">Execution depth across discovery, web research, providers, evidence and Agent OS — without replacing the existing pipeline.</p></div>
      <button aria-label="Action" className="btn-secondary" onClick={() => void load()}>Refresh</button>
    </header>

    <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {[
        ["Discovery catalog", overview?.fabric?.discovery_sources ?? "—", "inventory"],
        ["Enrichment catalog", overview?.fabric?.enrichment_catalog ?? "—", "definitions"],
        ["Executable adapters", overview?.fabric?.executable_enrichment_adapters ?? "—", "native / configured"],
        ["Agent contracts", overview?.fabric?.agents ?? "—", "registered"],
      ].map(([label, value, sub]) => <div key={label} className="card-pad"><div className="text-xs text-ink-faint">{label}</div><div className="mt-1 text-2xl font-semibold text-ink">{value}</div><div className="mt-1 text-[10px] uppercase tracking-wide text-ink-faint">{sub}</div></div>)}
    </section>

    <section className="grid gap-5 lg:grid-cols-[1fr_1.2fr]">
      <div className="premium-card p-5">
        <div className="label">Discovery Planner</div><h2 className="mt-1 text-lg font-semibold text-ink">Plan coverage before execution</h2>
        <div className="mt-4 space-y-3">
          <label className="block text-xs font-medium text-ink-faint">Vertical<input className="input mt-1" value={vertical} onChange={e => setVertical(e.target.value)} /></label>
          <label className="block text-xs font-medium text-ink-faint">Locations<input className="input mt-1" value={locations} onChange={e => setLocations(e.target.value)} /></label>
          <label className="block text-xs font-medium text-ink-faint">Query families<input className="input mt-1" value={queries} onChange={e => setQueries(e.target.value)} /></label>
          <button aria-label="Action" disabled={busy || !tid} onClick={createPlan} className="btn-primary w-full">{busy ? "Planning…" : "Generate coverage plan"}</button>
        </div>
        {plan && <pre className="mt-4 max-h-72 overflow-auto rounded-xl border border-line bg-surface-3 p-3 text-[11px] text-ink-muted">{JSON.stringify(plan, null, 2)}</pre>}
      </div>

      <div className="premium-card overflow-hidden">
        <div className="border-b border-line px-5 py-4"><div className="label">Provider execution truth</div><h2 className="mt-1 text-lg font-semibold text-ink">Configured vs executable</h2><p className="mt-1 text-xs text-ink-muted">Catalog entries are inventory; only native or explicitly configured adapters are executable.</p></div>
        <div className="max-h-[560px] overflow-auto divide-y divide-line">
          {providers.map(p => <div key={p.name} className="px-5 py-3">
            <div className="flex items-center justify-between gap-3"><span className="font-medium text-ink">{p.name}</span><span className={`pill ${p.executable ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "bg-surface-3 text-ink-faint"}`}>{p.executable ? "Executable" : "Catalog only"}</span></div>
            <div className="mt-1 text-[10px] text-ink-faint">{p.native_adapter ? "Native adapter" : p.endpoint_adapter ? "Configured endpoint adapter" : "No runtime adapter"} · {p.configured ? "credential configured" : "not configured"}</div>
          </div>)}
          {!providers.length && <div className="p-5 text-sm text-ink-muted">No provider catalog returned.</div>}
        </div>
      </div>
    </section>

    {message && <div className="rounded-xl border border-line bg-surface-2 px-4 py-3 text-sm text-ink-muted">{message}</div>}
    <div className="text-xs text-ink-faint">{executable.length} provider adapters are currently executable in this environment. This number changes with configured credentials/endpoints.</div>
  </div>;
}
