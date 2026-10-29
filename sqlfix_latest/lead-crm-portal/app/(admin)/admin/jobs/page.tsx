"use client";

/**
 * Job monitor — "why is this client's pipeline stuck".
 *
 * Also carries the cron audit, because the two questions arrive together:
 * a feature that looks broken is usually either a failed job or a trigger
 * that never fired.
 */
import { useLiveLoader } from "@/lib/live";
import { useCallback, useEffect, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { ErrorState, EmptyState, PollingDeadlineState } from "@/components/ui/States";
import { FreshnessNote } from "@/components/ui/SurfaceMeta";
import { PageHeader } from "@/components/ui/Primitives";

import { adminExtras, type CronCheck, type Job, type OperationalSnapshot, type QueueStats } from "@/lib/admin";

const STATUS: Record<string, string> = {
  queued: "bg-surface-3 text-ink-muted",
  running: "bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-700 dark:text-brand-300",
  done: "bg-signal-50 text-signal-700 dark:bg-signal-500/15 dark:text-signal-300",
  failed: "bg-rust-50 text-rust-700 dark:bg-rust-900/30 dark:text-rust-300",
  cancelled: "bg-surface-3 text-ink-faint",
};

const FILTERS = ["", "queued", "running", "failed", "done"];
const POLL_MS = 10_000;

function secs(v?: number | null) {
  if (v == null) return "—";
  if (v < 60) return `${Math.round(v)}s`;
  if (v < 3600) return `${Math.round(v / 60)}m`;
  return `${(v / 3600).toFixed(1)}h`;
}

function JobsMonitor() {
  const params = useSearchParams();
  const runId = params.get("run_id") || "";
  const [jobs, setJobs] = useState<Job[]>([]);
  const [stats, setStats] = useState<QueueStats>({});
  const [operational, setOperational] = useState<OperationalSnapshot | null>(null);
  const [cron, setCron] = useState<CronCheck[]>([]);
  const [status, setStatus] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState("");
  const [open, setOpen] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [pollingExpired, setPollingExpired] = useState(false);
  const [pollingReset, setPollingReset] = useState(0);
  const [resetForm, setResetForm] = useState(() => ({
    runId,
    tenantId: "",
    statuses: "queued,running,retrying",
    cutoff: "",
    apply: false,
    confirm: false,
    operationKey: "",
  }));
  const [resetNotice, setResetNotice] = useState("");

  const load = useCallback(async () => {
    try {
      setError(null);
      const [jobsResult, cronResult] = await Promise.allSettled([
        adminExtras.jobs({ status: status || undefined, run_id: runId || undefined, limit: 100 }),
        adminExtras.cronAudit(),
      ]);
      if (jobsResult.status === "rejected") throw jobsResult.reason;
      const d = jobsResult.value;
      setJobs(d.jobs || []);
      setStats(d.stats || {});
      setOperational(d.operational || null);
      if (cronResult.status === "fulfilled") setCron(cronResult.value.checks || []);
      setError(null);
    } catch (e: unknown) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [status, runId]);

  const refresh = () => {
    setPollingExpired(false);
    setPollingReset((value) => value + 1);
    void load();
  };

  useEffect(() => {
    setPollingExpired(false);
  }, [status]);
  useLiveLoader(load, POLL_MS, [status, pollingReset], { onDeadline: () => setPollingExpired(true) });

  async function act(id: string, fn: () => Promise<any>) {
    setBusy(id);
    try {
      await fn();
      await refresh();
    } catch (e: unknown) {
      setError(e);
    } finally {
      setBusy("");
    }
  }

  async function submitScopedReset() {
    const runScope = resetForm.runId.trim();
    const tenantScope = resetForm.tenantId.trim();
    if (Boolean(runScope) === Boolean(tenantScope)) {
      setResetNotice("Enter exactly one run ID or tenant ID.");
      return;
    }
    const statusAllowlist = resetForm.statuses.split(",").map((value) => value.trim()).filter(Boolean);
    if (!runScope && !statusAllowlist.length) {
      setResetNotice("Tenant scope requires at least one status.");
      return;
    }
    setBusy("scoped-reset");
    setResetNotice("");
    try {
      const result = await adminExtras.scopedWorkReset({
        operation_key: resetForm.operationKey.trim() || `ui-reset-${crypto.randomUUID()}`,
        scope_kind: runScope ? "run" : resetForm.cutoff.trim() ? "tenant_stale" : "tenant_active",
        run_id: runScope || undefined,
        tenant_id: tenantScope || undefined,
        status_allowlist: runScope ? undefined : statusAllowlist,
        cutoff: resetForm.cutoff.trim() || undefined,
        dry_run: !resetForm.apply,
        confirmation: resetForm.confirm,
      });
      setResetNotice(result?.reset?.outcome === "conflict" ? "Operation key conflict; no work was changed." : `${resetForm.apply ? "Apply" : "Dry-run"} completed. No broad reset is available.`);
    } catch (e: unknown) {
      setError(e);
    } finally {
      setBusy("");
    }
  }

  // A worker that died mid-job leaves the row 'running' forever; a growing
  // oldest-queued age means no worker is consuming at all.
  const stalled = (stats.oldest_queued_seconds || 0) > 900;

  return (
    <div className="page">
      <PageHeader eyebrow="Control plane" title="Jobs" description="Background queue for pipelines, sends, imports, and scheduled work. Visibility-aware refresh pauses in hidden tabs." meta={<FreshnessNote label="Live queue monitor" detail="refreshes while visible" />} actions={<div className="flex gap-2"><button type="button" aria-label="Reap stale jobs" className="btn-secondary" onClick={() => act("reap", () => adminExtras.reapJobs())}>Reap stale</button><button type="button" aria-label="Refresh jobs" className="btn-secondary" onClick={refresh}>Refresh</button></div>} />

      {loading ? <div className="card-pad text-sm text-ink-faint" aria-busy="true">Loading jobs…</div> : null}
      {error ? <ErrorState error={error} onRetry={refresh} /> : null}
      {pollingExpired ? <PollingDeadlineState onRefresh={refresh} /> : null}

      {operational && (
        <section className={`card-pad border-l-2 ${operational.ok ? "border-l-signal-500" : "border-l-rust-500"}`} aria-label="Queue operational status">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="text-sm font-medium text-ink">
                Queue {operational.ok ? "healthy" : "degraded"}
              </p>
              <p className="mt-0.5 text-xs text-ink-muted">
                Capability: {operational.queue?.capable ? "ready" : operational.queue?.reason || "unavailable"}
                {operational.queue?.oldest_queued_seconds != null ? ` · oldest ${secs(operational.queue.oldest_queued_seconds)}` : ""}
                {` · workers ${operational.workers?.fresh_count ?? 0}/${operational.workers?.count ?? 0} fresh`}
              </p>
            </div>
            <div className="text-right text-xs text-ink-faint">
              <div>{operational.outcomes?.retrying ?? 0} retrying · {operational.outcomes?.retry_exhausted ?? 0} exhausted</div>
              <div>{operational.outcomes?.stale_reaped ?? 0} stale reaped</div>
            </div>
          </div>
        </section>
      )}

      <section className="card-pad space-y-3" aria-labelledby="scoped-reset-title">
        <div>
          <h2 id="scoped-reset-title" className="text-sm font-semibold text-ink">Scoped work reset</h2>
          <p className="mt-1 text-xs text-ink-faint">Dry-run is the default. Select one run or one tenant scope; reset-all is not available.</p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-xs text-ink-muted">Run ID
            <input aria-label="Scoped reset run ID" className="input mt-1" value={resetForm.runId} onChange={(e) => setResetForm({ ...resetForm, runId: e.target.value, tenantId: "" })} placeholder="run-id" />
          </label>
          <label className="text-xs text-ink-muted">Tenant ID
            <input aria-label="Scoped reset tenant ID" className="input mt-1" value={resetForm.tenantId} onChange={(e) => setResetForm({ ...resetForm, tenantId: e.target.value, runId: "" })} placeholder="tenant-id" />
          </label>
          <label className="text-xs text-ink-muted">Statuses (tenant scope)
            <input aria-label="Scoped reset statuses" className="input mt-1" value={resetForm.statuses} onChange={(e) => setResetForm({ ...resetForm, statuses: e.target.value })} placeholder="queued,running,retrying" />
          </label>
          <label className="text-xs text-ink-muted">Cutoff (optional stale scope)
            <input aria-label="Scoped reset cutoff" className="input mt-1" value={resetForm.cutoff} onChange={(e) => setResetForm({ ...resetForm, cutoff: e.target.value })} placeholder="2026-10-25T00:00:00Z" />
          </label>
        </div>
        <label className="block text-xs text-ink-muted">Operation key (optional)
          <input aria-label="Scoped reset operation key" className="input mt-1" value={resetForm.operationKey} onChange={(e) => setResetForm({ ...resetForm, operationKey: e.target.value })} placeholder="generated when blank" />
        </label>
        <div className="flex flex-wrap items-center gap-4 text-xs text-ink-muted">
          <label className="inline-flex items-center gap-2"><input type="checkbox" checked={resetForm.apply} onChange={(e) => setResetForm({ ...resetForm, apply: e.target.checked, confirm: false })} /> Apply reset</label>
          {resetForm.apply ? <label className="inline-flex items-center gap-2"><input type="checkbox" checked={resetForm.confirm} onChange={(e) => setResetForm({ ...resetForm, confirm: e.target.checked })} /> I confirm this exact scope</label> : null}
          <button type="button" className="btn-secondary" disabled={busy === "scoped-reset" || (resetForm.apply && !resetForm.confirm)} onClick={submitScopedReset}>{busy === "scoped-reset" ? "Submitting…" : resetForm.apply ? "Apply scoped reset" : "Preview scoped reset"}</button>
        </div>
        {resetNotice ? <p role="status" className="text-xs text-ink-muted">{resetNotice}</p> : null}
      </section>

      {stalled && (
        <div className="card-pad border-l-2 border-l-rust-500">
          <p className="text-sm font-medium text-ink">Queue is backing up</p>
          <p className="mt-0.5 text-sm text-ink-muted">
            Oldest queued job has been waiting {secs(stats.oldest_queued_seconds)}. Check that a
            worker is running: <code className="font-mono text-xs">python -m backend.worker</code>
          </p>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {(["queued", "running", "done", "failed"] as const).map((k) => (
          <div key={k} className="card-pad">
            <div className="text-xs uppercase tracking-wide text-ink-faint">{k}</div>
            <div className="mt-1 text-2xl font-semibold text-ink">{stats[k] ?? 0}</div>
          </div>
        ))}
      </div>

      <section className="space-y-2">
        <h2 className="text-sm font-semibold text-ink">Scheduled work</h2>
        <p className="text-xs text-ink-faint">
          Every feature here fails silently when its trigger is misconfigured, so age since last run
          is the only reliable signal.
        </p>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {cron.map((c) => (
            <div key={c.feature} className="card-pad">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm text-ink">{c.feature}</span>
                <span
                  className={`pill ${
                    c.status === "ok"
                      ? STATUS.done
                      : c.status === "never_run"
                        ? STATUS.cancelled
                        : STATUS.failed
                  }`}
                >
                  {c.status}
                </span>
              </div>
              <div className="mt-1 text-xs text-ink-faint">
                {c.age_hours == null ? "never" : `${c.age_hours}h ago`} · expected every{" "}
                {c.expected_every_hours}h
              </div>
            </div>
          ))}
        </div>
      </section>

      <div className="flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <button type="button" aria-label={`Filter jobs by ${f || "all statuses"}`}
            onClick={() => setStatus(f)}
            className={
              status === f
                ? "pill bg-signal-600 text-white"
                : "pill border border-line bg-surface-2 text-ink-muted"
            }
          >
            {f || "All"}
          </button>
        ))}
      </div>

      <div className="space-y-2">
        {jobs.map((j) => (
          <div key={j.id} className="card-pad">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`pill ${STATUS[j.status] || STATUS.queued}`}>{j.status}</span>
                  <span className="font-mono text-xs text-ink">{j.kind}</span>
                  {j.attempts > 1 && (
                    <span className="text-[11px] text-ink-faint">
                      attempt {j.attempts}/{j.max_attempts}
                    </span>
                  )}
                  {j.priority !== 0 && (
                    <span className="text-[11px] text-ink-faint">prio {j.priority}</span>
                  )}
                </div>
                <div className="mt-1 font-mono text-[11px] text-ink-faint">{j.id}</div>
                {j.error ? (
                  <p className="mt-1 text-sm text-rust-600 dark:text-rust-300">{j.error}</p>
                ) : null}
              </div>
              <div className="shrink-0 space-y-1 text-right">
                <div className="text-xs text-ink-faint">
                  {new Date(j.created_at).toLocaleString()}
                </div>
                <div className="flex justify-end gap-2 text-xs">
                  <button
                    className="text-ink-faint hover:text-ink"
                    onClick={() => setOpen(open === j.id ? "" : j.id)}
                  >
                    {open === j.id ? "Hide" : "Payload"}
                  </button>
                  {(j.status === "failed" || j.status === "cancelled") && (
                    <button
                      disabled={busy === j.id}
                      className="font-medium text-brand-600 hover:underline disabled:opacity-40 dark:text-brand-700 dark:text-brand-300"
                      onClick={() => act(j.id, () => adminExtras.retryJob(j.id))}
                    >
                      Retry
                    </button>
                  )}
                  {j.status === "queued" && (
                    <button
                      disabled={busy === j.id}
                      className="text-rust-600 hover:underline disabled:opacity-40 dark:text-rust-300"
                      onClick={() => act(j.id, () => adminExtras.cancelJob(j.id))}
                    >
                      Cancel
                    </button>
                  )}
                </div>
              </div>
            </div>
            {open === j.id && (
              <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-surface-3 p-2 text-[11px] text-ink-muted">
                {JSON.stringify({ payload: j.payload, result: j.result }, null, 2)}
              </pre>
            )}
          </div>
        ))}
        {!jobs.length && <div className="card"><EmptyState title="No jobs match this filter" hint={status || runId ? "Clear the filter to inspect the wider queue." : "New pipeline and delivery work will appear here when enqueued."} /></div>}
      </div>
    </div>
  );
}

export default function AdminJobsPage() {
  return (
    <Suspense fallback={<div className="page text-sm text-ink-faint">Loading jobs…</div>}>
      <JobsMonitor />
    </Suspense>
  );
}
