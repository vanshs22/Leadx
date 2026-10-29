"use client";

import { useEffect, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { admin } from "@/lib/admin";
import { useLiveLoader } from "@/lib/live";
import { useCrmRealtime } from "@/lib/crmRealtime";
import { ErrorState } from "@/components/ui/States";
import { FreshnessNote } from "@/components/ui/SurfaceMeta";
import { OperationStateCard, toOperationState } from "@/components/ui/OperationState";

const TERMINAL_STATUSES = new Set(["completed", "complete", "done", "failed", "cancelled", "partial"]);

function statusBadgeClass(status: string) {
  switch (status) {
    case "completed":
    case "complete":
    case "done":
      return "rounded-full bg-signal-500/15 px-2.5 py-0.5 text-xs font-medium text-signal-400";
    case "failed":
      return "rounded-full bg-rust-500/15 px-2.5 py-0.5 text-xs font-medium text-rust-400";
    case "partial":
      return "rounded-full bg-amber-500/15 px-2.5 py-0.5 text-xs font-medium text-amber-400";
    case "queued":
      return "rounded-full bg-sky-500/15 px-2.5 py-0.5 text-xs font-medium text-sky-400";
    case "running":
      return "rounded-full bg-brand-500/15 px-2.5 py-0.5 text-xs font-medium text-brand-400";
    default:
      return "rounded-full bg-ink/10 px-2.5 py-0.5 text-xs font-medium text-ink-muted";
  }
}

function PipelineForm() {
  const params = useSearchParams();
  const [tenants, setTenants] = useState<any[]>([]);
  const [tenantLoading, setTenantLoading] = useState(true);
  const [tenantError, setTenantError] = useState<unknown>(null);
  const [icps, setIcps] = useState<any[]>([]);
  const [form, setForm] = useState({
    tenant_id: params.get("tenant") || "",
    icp_id: "",
    industry: "medspa",
    locations: "Hoboken NJ, Jersey City NJ",
    limit_per_location: 5,
    target_new_leads: 5,
    deliver: true,
    allow_tier_b: false,
    allow_tier_c: true,
    skip_enrichment: false,
    include_maps: true,
    include_web: true,
    include_yelp: false,
    include_facebook: false,
    include_linkedin: false,
    include_instagram: false,
  });
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [liveRun, setLiveRun] = useState<any>(null);
  // Hydrate from /admin/pipeline?run_id=... and only replace it with a POST id.
  const [liveRunId, setLiveRunId] = useState(() => params.get("run_id") || "");
  const [pollError, setPollError] = useState<unknown>(null);
  const [pollingExpired, setPollingExpired] = useState(false);
  const [pollingReset, setPollingReset] = useState(0);

  async function loadTenants() {
    setTenantLoading(true);
    try {
      const d = await admin.tenants("active");
      setTenants(d.tenants || []);
      setTenantError(null);
    } catch (caught: unknown) {
      setTenantError(caught);
    } finally {
      setTenantLoading(false);
    }
  }

  useEffect(() => {
    void loadTenants();
  }, []);

  useEffect(() => {
    if (!form.tenant_id) return;
    admin.icps(form.tenant_id).then((d) => setIcps(d.icps || [])).catch(() => {});
  }, [form.tenant_id]);

  const runStatus = String(liveRun?.status || "").toLowerCase();
  const runTerminal = TERMINAL_STATUSES.has(runStatus);

  async function loadLiveRun() {
    if (!liveRunId) return;
    try {
      // Always fetch the server record, including when a local snapshot says terminal.
      const d = await admin.getRun(liveRunId);
      const run = d?.run || d;
      if (!run || typeof run !== "object") {
        setLiveRun(null);
        setPollError(new Error(`Run ${liveRunId} was not found. Refresh or start a new pipeline run.`));
        return;
      }
      setPollError(null);
      setLiveRun(run);
      const nextStatus = String((run as any).status || "").toLowerCase();
      if (TERMINAL_STATUSES.has(nextStatus)) setBusy(false);
    } catch (e: unknown) {
      // Never keep a fabricated or stale progress snapshot after an authoritative read fails.
      setLiveRun(null);
      setPollError(e);
    }
  }

  const refreshRun = () => {
    setPollingExpired(false);
    setPollError(null);
    setPollingReset((value) => value + 1);
  };

  useLiveLoader(loadLiveRun, liveRunId && !runTerminal ? 2_000 : 0, [liveRunId, pollingReset, runTerminal], {
    deadlineMs: 0,
    onDeadline: () => setPollingExpired(true),
  });
  // Admin progress still comes from the platform-authorized GET. The private
  // tenant channel only tells this surface when to refetch that projection.
  useCrmRealtime(() => { void loadLiveRun(); }, form.tenant_id);

  async function run() {
    if (!form.tenant_id) {
      setError("Select a client first");
      return;
    }
    setBusy(true);
    setError("");
    setPollError(null);
    setPollingExpired(false);
    setResult(null);
    setLiveRun(null);
    try {
      const locations = form.locations
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      const total = Math.min(500, Math.max(1, Number(form.target_new_leads) || 5));
      const perLoc = Math.max(1, Number(form.limit_per_location) || 5);
      const r = await admin.runPipeline({
        tenant_id: form.tenant_id,
        icp_id: form.icp_id || undefined,
        industry: form.industry,
        locations,
        limit_per_location: perLoc,
        target_new_leads: total,
        deliver: form.deliver,
        allow_tier_b: form.allow_tier_b,
        allow_tier_c: form.allow_tier_c,
        skip_enrichment: form.skip_enrichment,
        include_maps: form.include_maps,
        include_web: form.include_web,
        include_yelp: form.include_yelp,
        include_facebook: form.include_facebook,
        exhaustive_discovery: total > 25,
        include_linkedin: form.include_linkedin,
        include_instagram: form.include_instagram,
      }, `admin-pipeline:${crypto.randomUUID()}`);
      setResult(r);
      const rid = r?.run_id || r?.id;
      if (rid) {
        // The authoritative GET below supplies status and counters; do not invent them here.
        setLiveRunId(String(rid));
      } else {
        setLiveRunId("");
        setBusy(false);
        setError("Pipeline response did not include a run ID, so live progress cannot be loaded.");
      }
    } catch (e: unknown) {
      setError(e);
      setBusy(false);
    }
  }

  async function runAll() {
    setBusy(true);
    setError("");
    try {
      const r = await admin.runAllIcps({
        limit_per_location: Number(form.limit_per_location),
      });
      setResult(r);
    } catch (e: unknown) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const status = String(liveRun?.status || "").toLowerCase();
  const operation = liveRun
    ? toOperationState(liveRun, { runId: liveRunId, deadlineReached: pollingExpired })
    : null;

  return (
    <div className="page">
      <header>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-ink">Run pipeline</h1>
            <p className="mt-1 max-w-3xl text-sm text-ink-faint">
              Discover → enrich → gate → deliver for one client. Live counters and status are read from the authoritative run record.
            </p>
          </div>
          <FreshnessNote label="Live operation monitor" detail="2-second visible refresh while a run is active" />
        </div>
        {tenantError ? <ErrorState error={tenantError} onRetry={loadTenants} /> : null}
        {pollError ? (
          <div role="alert" className="mt-3 space-y-2">
            <p className="text-sm font-medium text-rust-400">Unable to read live run status</p>
            <ErrorState error={pollError} onRetry={refreshRun} />
          </div>
        ) : null}
      </header>

      <div className="mt-5 grid gap-4 lg:grid-cols-[minmax(260px,0.8fr)_minmax(0,1.7fr)] lg:items-start">
        <section aria-labelledby="pipeline-setup-title" className="space-y-3 rounded-xl border border-line bg-surface-2/60 p-5">
          <div>
            <h2 id="pipeline-setup-title" className="text-sm font-semibold text-ink">Run setup</h2>
            <p className="mt-1 text-xs text-ink-faint">Choose the client, target, and source lanes.</p>
          </div>
          <label className="block text-xs text-ink-faint">
            Client
            <select aria-label="Client" className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink" value={form.tenant_id} onChange={(e) => setForm({ ...form, tenant_id: e.target.value, icp_id: "" })}>
              {tenantLoading ? <option disabled>Loading clients…</option> : null}
              {!tenantLoading && tenants.length === 0 ? <option disabled>No active clients returned</option> : null}
              {!tenantLoading && tenants.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </label>
          <label className="block text-xs text-ink-faint">
            ICP (optional)
            <select aria-label="ICP (optional)" className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink" value={form.icp_id} onChange={(e) => setForm({ ...form, icp_id: e.target.value })}>
              <option value="">Manual industry / locations</option>
              {icps.map((i) => <option key={i.id} value={i.id}>{i.vertical} — {(i.geo || []).join(", ")}</option>)}
            </select>
          </label>
          <label className="block text-xs text-ink-faint">
            Industry
            <input aria-label="Industry" className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink" value={form.industry} onChange={(e) => setForm({ ...form, industry: e.target.value })} />
          </label>
          <label className="block text-xs text-ink-faint">
            Locations (comma-separated)
            <input aria-label="Locations (comma-separated)" className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink" value={form.locations} onChange={(e) => setForm({ ...form, locations: e.target.value })} />
          </label>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
            <label className="block text-xs text-ink-faint">
              Limit per location
              <input aria-label="Limit per location" type="number" min={1} className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink" value={form.limit_per_location} onChange={(e) => setForm({ ...form, limit_per_location: Number(e.target.value) })} />
            </label>
            <label className="block text-xs text-ink-faint">
              Total leads this run
              <input aria-label="Total leads this run" type="number" min={1} max={500} className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink" value={form.target_new_leads} onChange={(e) => setForm({ ...form, target_new_leads: Math.min(500, Math.max(1, Number(e.target.value) || 1)) })} />
            </label>
          </div>
          <p className="text-[11px] text-ink-faint">Target at least 1 lead. Limit per location applies to each city.</p>

          <div className="rounded-lg border border-line bg-surface px-3 py-3">
            <div className="mb-2 text-xs font-semibold text-ink">Lead sources (multi-select)</div>
            <p className="mb-2 text-[11px] text-ink-faint">Only selected sources run. Small targets use reserved slots so Maps cannot take 100%.</p>
            <div className="flex flex-wrap gap-3 text-sm text-ink">
              {([
                ["include_maps", "Maps"], ["include_web", "Web"], ["include_yelp", "Yelp"],
                ["include_facebook", "Facebook"], ["include_linkedin", "LinkedIn"], ["include_instagram", "Instagram"],
              ] as const).map(([key, label]) => (
                <label key={key} className="flex items-center gap-2"><input aria-label={label} type="checkbox" checked={(form as any)[key]} onChange={(e) => setForm({ ...form, [key]: e.target.checked })} />{label}</label>
              ))}
            </div>
          </div>
          <div className="flex flex-wrap gap-4 text-sm text-ink-faint">
            <label className="flex items-center gap-2"><input aria-label="Deliver" type="checkbox" checked={form.deliver} onChange={(e) => setForm({ ...form, deliver: e.target.checked })} />Deliver</label>
            <label className="flex items-center gap-2"><input aria-label="Allow Tier B" type="checkbox" checked={form.allow_tier_b} onChange={(e) => setForm({ ...form, allow_tier_b: e.target.checked })} />Allow Tier B</label>
            <label className="flex items-center gap-2"><input aria-label="Allow Tier C" type="checkbox" checked={form.allow_tier_c} onChange={(e) => setForm({ ...form, allow_tier_c: e.target.checked })} />Allow Tier C</label>
            <label className="flex items-center gap-2"><input aria-label="Skip enrichment" type="checkbox" checked={form.skip_enrichment} onChange={(e) => setForm({ ...form, skip_enrichment: e.target.checked })} />Skip enrichment</label>
          </div>
          <div className="flex flex-wrap gap-2 pt-2">
            <button type="button" onClick={run} disabled={tenantLoading || busy || !form.tenant_id} className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{busy ? "Running…" : "Run for client"}</button>
            <button type="button" onClick={runAll} disabled={busy} className="rounded-lg border border-line px-4 py-2 text-sm text-ink-muted disabled:opacity-50">Run all active ICPs</button>
          </div>
        </section>

        <section aria-labelledby="pipeline-monitor-title" className="min-w-0 space-y-3">
          <div className="rounded-xl border border-line bg-surface-2/60 p-4">
            <h2 id="pipeline-monitor-title" className="text-sm font-semibold text-ink">Live monitor</h2>
            <p className="mt-1 text-xs text-ink-faint">The monitor appears only after an authoritative run record is returned.</p>
          </div>
          {liveRun ? (
            <>
              {operation ? <OperationStateCard operation={operation} title="Live run and queue state" onRefresh={refreshRun} jobsHref={liveRunId ? `/admin/jobs?run_id=${encodeURIComponent(liveRunId)}` : undefined} /> : null}
              <div className="rounded-2xl border border-line bg-surface-2/50 p-4">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                  <div><h2 className="text-sm font-semibold text-ink">Live run progress</h2><p className="font-mono text-xs text-ink-faint">{liveRun.id || liveRunId}</p></div>
                  <span className={statusBadgeClass(status)}>{status || "unknown"}</span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[480px] text-left text-sm">
                    <thead><tr className="border-b border-line text-xs uppercase tracking-wide text-ink-faint"><th className="py-2 pr-3 font-medium">Stage</th><th className="py-2 pr-3 font-medium">Count</th><th className="py-2 font-medium">Notes</th></tr></thead>
                    <tbody className="text-ink">
                      {[["Discovered", "discovered", "Businesses found", operation?.discovered], ["Enriched", "enriched", "Website / contact data", operation?.enriched], ["Verified", "verified", "Contact and business checks", operation?.verified], ["Scored", "scored", "Lead quality score", operation?.scored], ["Gated (quality)", "gated_ok", "Passed quality gate", operation?.gated], ["Assigned", "assigned", "Assigned to this client", operation?.assigned], ["Delivered", "delivered", "Sent to channel / CRM", operation?.delivered]].map(([label, key, note, value], index, rows) => (
                        <tr key={key} className={index === rows.length - 1 ? "" : "border-b border-line/60"}><td className="py-2.5 pr-3">{label}</td><td className="py-2.5 pr-3 font-semibold tabular-nums">{value ?? "—"}</td><td className="py-2.5 text-xs text-ink-faint">{note}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {liveRun.meta?.message ? <p className="mt-3 text-xs text-ink-faint">{String(liveRun.meta.message)}</p> : null}
                {liveRun.error_message ? <p className="mt-2 text-xs text-rust-400">{liveRun.error_message}</p> : null}
              </div>
            </>
          ) : (
            <div className="rounded-2xl border border-dashed border-line bg-surface-2/30 p-6 text-sm text-ink-muted" role="status" aria-live="polite">
              {liveRunId ? `Loading authoritative run ${liveRunId}…` : "No run selected. Start a pipeline run or open this page with a run_id."}
            </div>
          )}
        </section>
      </div>

      {error ? <ErrorState error={error} onRetry={refreshRun} /> : null}
      {result && !liveRun && !error ? <p className="rounded-xl border border-line bg-surface-2/60 p-4 text-sm text-ink-muted" role="status">Pipeline request accepted. Waiting for the authoritative run record…</p> : null}
    </div>
  );
}

export default function AdminPipelinePage() {
  return <Suspense fallback={<div className="p-8 text-ink-faint">Loading…</div>}><PipelineForm /></Suspense>;
}
