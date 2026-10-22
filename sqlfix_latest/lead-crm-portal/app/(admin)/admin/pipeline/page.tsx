"use client";

import { useEffect, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { admin } from "@/lib/admin";
import { useLiveLoader } from "@/lib/live";
import { ErrorState, EmptyState } from "@/components/ui/States";
import { FreshnessNote, UnavailableNote } from "@/components/ui/SurfaceMeta";
import { OperationStateCard, toOperationState } from "@/components/ui/OperationState";

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
    target_new_leads: 50,
    deliver: true,
    allow_tier_b: false,
    skip_enrichment: false,
    include_maps: true,
    include_web: true,
    include_yelp: true,
    include_facebook: true,
    include_linkedin: true,
    include_instagram: true,
  });
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [liveRun, setLiveRun] = useState<any>(null);
  const [liveRunId, setLiveRunId] = useState("");
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
  const runTerminal = ["completed", "complete", "done", "failed", "cancelled"].includes(runStatus);

  async function loadLiveRun() {
    if (!liveRunId || runTerminal) return;
    try {
      const d = await admin.getRun(liveRunId);
      const run = d?.run || d;
      if (!run) return;
      setPollError(null);
      setLiveRun(run);
      const nextStatus = String(run.status || "").toLowerCase();
      if (["completed", "complete", "done", "failed", "cancelled"].includes(nextStatus)) setBusy(false);
    } catch (e: unknown) {
      setPollError(e);
    }
  }

  const refreshRun = () => {
    setPollingExpired(false);
    setPollingReset((value) => value + 1);
  };

  useLiveLoader(loadLiveRun, 2_000, [liveRunId, pollingReset, runTerminal], {
    onDeadline: () => setPollingExpired(true),
  });

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
    try {
      const locations = form.locations
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      const total = Math.max(0, Number(form.target_new_leads) || 0);
      const perLoc = Math.max(1, Number(form.limit_per_location) || 30);
      const r = await admin.runPipeline({
        tenant_id: form.tenant_id,
        icp_id: form.icp_id || undefined,
        industry: form.industry,
        locations,
        limit_per_location: perLoc,
        target_new_leads: total,
        deliver: form.deliver,
        allow_tier_b: form.allow_tier_b,
        skip_enrichment: form.skip_enrichment,
        include_maps: form.include_maps,
        include_web: form.include_web,
        include_yelp: form.include_yelp,
        include_facebook: form.include_facebook,
        exhaustive_discovery: total > 25,
        include_linkedin: form.include_linkedin,
        include_instagram: form.include_instagram,
      });
      setResult(r);
      const rid = r?.run_id || r?.id;
      if (rid) {
        setLiveRunId(rid);
        setLiveRun({
          id: rid,
          status: r?.status || "started",
          discovered: 0,
          enriched: 0,
          gated_ok: 0,
          assigned: 0,
          delivered: 0,
        });
      } else {
        setBusy(false);
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
        <h1 className="text-2xl font-semibold text-ink">Run pipeline</h1>
        <p className="mt-1 text-sm text-ink-faint">
          Discover → enrich → gate → deliver for one client. Run state below is sourced from the returned run record; no progress is invented while the API is unavailable.
        </p>
        <div className="mt-2"><FreshnessNote label="Live operation monitor" detail="2-second visible refresh while a run is active" /></div>
        {tenantError ? <ErrorState error={tenantError} onRetry={loadTenants} /> : null}
        {pollError ? (
          <div role="alert" className="space-y-2">
            <p className="text-sm font-medium text-rust-400">Unable to read live run status</p>
            <ErrorState error={pollError} onRetry={refreshRun} />
          </div>
        ) : null}
      </header>

      <div className="max-w-xl space-y-3 rounded-xl border border-line bg-surface-2/60 p-5">
        <label className="block text-xs text-ink-faint">
          Client
          <select
            className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink"
            value={form.tenant_id}
            onChange={(e) => setForm({ ...form, tenant_id: e.target.value, icp_id: "" })}
          >
            {tenantLoading ? <option disabled>Loading clients…</option> : null}
            {!tenantLoading && tenants.length === 0 ? <option disabled>No active clients returned</option> : null}
            {!tenantLoading && tenants.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>

        <label className="block text-xs text-ink-faint">
          ICP (optional)
          <select
            className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink"
            value={form.icp_id}
            onChange={(e) => setForm({ ...form, icp_id: e.target.value })}
          >
            <option value="">Manual industry / locations</option>
            {icps.map((i) => (
              <option key={i.id} value={i.id}>
                {i.vertical} — {(i.geo || []).join(", ")}
              </option>
            ))}
          </select>
        </label>

        <label className="block text-xs text-ink-faint">
          Industry
          <input
            className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink"
            value={form.industry}
            onChange={(e) => setForm({ ...form, industry: e.target.value })}
          />
        </label>

        <label className="block text-xs text-ink-faint">
          Locations (comma-separated)
          <input
            className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink"
            value={form.locations}
            onChange={(e) => setForm({ ...form, locations: e.target.value })}
          />
        </label>

        <label className="block text-xs text-ink-faint">
          Limit per location
          <input
            type="number"
            min={1}
            className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink"
            value={form.limit_per_location}
            onChange={(e) =>
              setForm({ ...form, limit_per_location: Number(e.target.value) })
            }
          />
        </label>

        <label className="block text-xs text-ink-faint">
          Total leads this run
          <input
            type="number"
            min={0}
            max={50000}
            className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink"
            value={form.target_new_leads}
            onChange={(e) =>
              setForm({ ...form, target_new_leads: Number(e.target.value) })
            }
          />
          <span className="mt-1 block text-xs text-ink-faint">
            Cap how many businesses to find in this run (e.g. 50). Uses continuous
            discovery when set. Limit per location still applies per city.
          </span>
        </label>

        <div className="rounded-lg border border-line bg-surface px-3 py-3">
          <div className="text-xs font-semibold text-ink mb-2">Lead sources (multi-select)</div>
          <p className="text-[11px] text-ink-faint mb-2">
            Only selected sources run. Small targets use reserved slots so Maps cannot take 100%.
          </p>
          <div className="flex flex-wrap gap-3 text-sm text-ink">
            {(
              [
                ["include_maps", "Maps"],
                ["include_web", "Web"],
                ["include_yelp", "Yelp"],
                ["include_facebook", "Facebook"],
                ["include_linkedin", "LinkedIn"],
                ["include_instagram", "Instagram"],
              ] as const
            ).map(([key, label]) => (
              <label key={key} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={(form as any)[key]}
                  onChange={(e) => setForm({ ...form, [key]: e.target.checked })}
                />
                {label}
              </label>
            ))}
          </div>
        </div>

        <div className="flex flex-wrap gap-4 text-sm text-ink-faint">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={form.deliver}
              onChange={(e) => setForm({ ...form, deliver: e.target.checked })}
            />
            Deliver
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={form.allow_tier_b}
              onChange={(e) => setForm({ ...form, allow_tier_b: e.target.checked })}
            />
            Allow Tier B
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={form.skip_enrichment}
              onChange={(e) => setForm({ ...form, skip_enrichment: e.target.checked })}
            />
            Skip enrichment
          </label>
        </div>

        <div className="flex gap-2 pt-2">
          <button
            type="button"
            onClick={run}
            disabled={tenantLoading || busy || !form.tenant_id}
            className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {busy ? "Running…" : "Run for client"}
          </button>
          <button
            type="button"
            onClick={runAll}
            disabled={busy}
            className="rounded-lg border border-line px-4 py-2 text-sm text-ink-muted disabled:opacity-50"
          >
            Run all active ICPs
          </button>
        </div>
      </div>

      {liveRun && (
        <>
          {operation ? (
            <OperationStateCard
              operation={operation}
              title="Live run and queue state"
              onRefresh={refreshRun}
              jobsHref={liveRunId ? `/admin/jobs?run_id=${encodeURIComponent(liveRunId)}` : undefined}
            />
          ) : null}
          <div className="rounded-2xl border border-line bg-surface-2/50 p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-sm font-semibold text-ink">Live run progress</h2>
              <p className="font-mono text-xs text-ink-faint">
                {liveRun.id || liveRunId}
              </p>
            </div>
            <span
              className={
                status === "completed"
                  ? "rounded-full bg-signal-500/15 px-2.5 py-0.5 text-xs font-medium text-signal-400"
                  : status === "failed"
                    ? "rounded-full bg-rust-500/15 px-2.5 py-0.5 text-xs font-medium text-rust-400"
                    : "rounded-full bg-brand-500/15 px-2.5 py-0.5 text-xs font-medium text-brand-400"
              }
            >
              {liveRun.status || "running"}
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[480px] text-left text-sm">
              <thead>
                <tr className="border-b border-line text-xs uppercase tracking-wide text-ink-faint">
                  <th className="py-2 pr-3 font-medium">Stage</th>
                  <th className="py-2 pr-3 font-medium">Count</th>
                  <th className="py-2 font-medium">Notes</th>
                </tr>
              </thead>
              <tbody className="text-ink">
                <tr className="border-b border-line/60">
                  <td className="py-2.5 pr-3">Discovered</td>
                  <td className="py-2.5 pr-3 font-semibold tabular-nums">
                    {liveRun.discovered ?? 0}
                  </td>
                  <td className="py-2.5 text-xs text-ink-faint">Businesses found</td>
                </tr>
                <tr className="border-b border-line/60">
                  <td className="py-2.5 pr-3">Enriched</td>
                  <td className="py-2.5 pr-3 font-semibold tabular-nums">
                    {liveRun.enriched ?? 0}
                  </td>
                  <td className="py-2.5 text-xs text-ink-faint">Website / contact data</td>
                </tr>
                <tr className="border-b border-line/60">
                  <td className="py-2.5 pr-3">Gated (quality)</td>
                  <td className="py-2.5 pr-3 font-semibold tabular-nums">
                    {liveRun.gated_ok ?? 0}
                  </td>
                  <td className="py-2.5 text-xs text-ink-faint">Passed quality gate</td>
                </tr>
                <tr className="border-b border-line/60">
                  <td className="py-2.5 pr-3">Assigned</td>
                  <td className="py-2.5 pr-3 font-semibold tabular-nums">
                    {liveRun.assigned ?? 0}
                  </td>
                  <td className="py-2.5 text-xs text-ink-faint">Assigned to this client</td>
                </tr>
                <tr>
                  <td className="py-2.5 pr-3">Delivered</td>
                  <td className="py-2.5 pr-3 font-semibold tabular-nums">
                    {liveRun.delivered ?? 0}
                  </td>
                  <td className="py-2.5 text-xs text-ink-faint">Sent to channel / CRM</td>
                </tr>
              </tbody>
            </table>
          </div>
          {liveRun.meta?.message ? (
            <p className="mt-3 text-xs text-ink-faint">{String(liveRun.meta.message)}</p>
          ) : null}
          {liveRun.error_message ? (
            <p className="mt-2 text-xs text-rust-400">{liveRun.error_message}</p>
          ) : null}
          </div>
        </>
      )}

      {error ? <ErrorState error={error} onRetry={refreshRun} /> : null}
      {result && !liveRun ? (
        <p className="rounded-xl border border-line bg-surface-2/60 p-4 text-sm text-ink-muted" role="status">
          Pipeline request accepted. Live progress will appear when the run identity is available.
        </p>
      ) : null}
    </div>
  );
}

export default function AdminPipelinePage() {
  return (
    <Suspense fallback={<div className="p-8 text-ink-faint">Loading…</div>}>
      <PipelineForm />
    </Suspense>
  );
}
