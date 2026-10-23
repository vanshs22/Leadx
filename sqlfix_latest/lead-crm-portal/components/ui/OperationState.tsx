import Link from "next/link";
import { PollingDeadlineState } from "./States";

export type OperationState = {
  runId?: string;
  jobId?: string;
  status: string;
  stage?: string;
  lastProgressAt?: string;
  queueAgeSeconds?: number;
  retries?: number;
  workerStatus?: string;
  reasonCode?: string;
  requestId?: string;
  discovered?: number;
  stored?: number;
  enriched?: number;
  verified?: number;
  scored?: number;
  gated?: number;
  assigned?: number;
  delivered?: number;
  target?: number;
  effectiveTarget?: number;
  shortfall?: number;
  blockers?: string[];
  reuse?: Record<string, unknown>;
  freshness?: Record<string, unknown>;
  sourceLanes?: Record<string, unknown>;
  terminalReason?: string;
  deadlineReached?: boolean;
};

type RecordLike = Record<string, unknown>;

function record(value: unknown): RecordLike {
  return value && typeof value === "object" ? value as RecordLike : {};
}

function text(value: unknown): string | undefined {
  return value == null || value === "" ? undefined : String(value);
}

function number(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function first(...values: unknown[]) {
  return values.find((value) => value != null && value !== "");
}

/** Normalize request, run, and job payloads into the same visible operation contract. */
export function toOperationState(value: unknown, fallback: Partial<OperationState> = {}): OperationState {
  const source = record(value);
  const run = record(first(source.run, source.operation, source.progress));
  const job = record(first(source.job, source.job_state));
  const progress = record(first(source.progress, source.funnel, run.progress));
  const request = record(source.request);
  const meta = record(first(source.meta, run.meta));
  const status = text(first(source.status, run.status, job.status, request.status, fallback.status)) || "queued";
  const objectValue = (...values: unknown[]) => {
    const value = first(...values);
    return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
  };
  const listValue = (...values: unknown[]) => {
    const value = first(...values);
    if (Array.isArray(value)) return value.map(String);
    return value == null || value === "" ? undefined : [String(value)];
  };
  return {
    runId: text(first(source.run_id, source.runId, source.pipeline_run_id, run.id, run.run_id, fallback.runId)),
    jobId: text(first(source.job_id, source.jobId, job.id, job.job_id, run.job_id, fallback.jobId)),
    status,
    stage: text(first(source.stage, source.current_stage, run.stage, meta.stage, job.stage, fallback.stage)),
    lastProgressAt: text(first(source.last_progress_at, source.lastProgressAt, run.last_progress_at, run.updated_at, job.last_progress_at, fallback.lastProgressAt)),
    queueAgeSeconds: number(first(source.queue_age_seconds, source.queueAgeSeconds, run.queue_age_seconds, job.queue_age_seconds, fallback.queueAgeSeconds)),
    retries: number(first(source.retries, source.retry_count, run.retries, job.attempts, fallback.retries)),
    workerStatus: text(first(source.worker_status, source.workerStatus, run.worker_status, job.worker_status, fallback.workerStatus)),
    reasonCode: text(first(source.reason_code, source.reasonCode, run.reason_code, job.reason_code, meta.reason_code, fallback.reasonCode)),
    requestId: text(first(source.request_id, source.requestId, request.id, fallback.requestId)),
    discovered: number(first(source.discovered, progress.discovered, run.discovered, request.discovered_count)),
    stored: number(first(source.stored, progress.stored, run.stored, request.global_stored_count)),
    enriched: number(first(source.enriched, progress.enriched, run.enriched, request.enriched_count)),
    verified: number(first(source.verified, progress.verified, run.verified, request.verified_count, meta.verified_count)),
    scored: number(first(source.scored, progress.scored, run.scored, request.scored_count, meta.scored_count)),
    gated: number(first(source.gated, progress.gated, run.gated_ok, request.gated_count)),
    assigned: number(first(source.assigned, progress.assigned, run.assigned, request.assigned_count)),
    delivered: number(first(source.delivered, progress.delivered, run.delivered, request.delivered_count)),
    target: number(first(source.target, source.requested_target, progress.target, run.requested_target, request.requested_target, request.requested_leads)),
    effectiveTarget: number(first(source.effective_target, progress.effective_target, run.effective_target, request.effective_target, request.approved_leads)),
    shortfall: number(first(source.shortfall, progress.shortfall, run.shortfall, meta.shortfall, request.shortfall)),
    blockers: listValue(source.blockers, source.assignment_blockers, progress.blockers, meta.blockers, meta.assignment_blockers, request.blockers, request.assignment_blockers),
    reuse: objectValue(source.reuse, source.reuse_counts, progress.reuse, meta.reuse_counts, request.reuse_counts),
    freshness: objectValue(source.freshness, progress.freshness, meta.freshness, request.freshness),
    sourceLanes: objectValue(source.source_lanes, source.lane_telemetry, progress.source_lanes, meta.source_lanes, meta.lane_telemetry),
    terminalReason: text(first(source.terminal_reason, progress.terminal_reason, run.terminal_reason, meta.terminal_reason, meta.stop_reason, source.error_message, fallback.reasonCode)),
    deadlineReached: fallback.deadlineReached,
  };
}

function age(seconds?: number) {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function label(value?: string) {
  return value ? value.replace(/[_-]+/g, " ") : "—";
}

function DisplayValue({ value }: { value: unknown }) {
  if (value == null) return <span>—</span>;
  if (typeof value === "object") return <span>{JSON.stringify(value)}</span>;
  return <span>{String(value)}</span>;
}

function progressRows(operation: OperationState) {
  return [
    ["Discovered", operation.discovered], ["Stored", operation.stored], ["Enriched", operation.enriched],
    ["Verified", operation.verified], ["Scored", operation.scored], ["Gated", operation.gated],
    ["Assigned", operation.assigned], ["Delivered", operation.delivered],
    ["Target", operation.target], ["Effective target", operation.effectiveTarget], ["Shortfall", operation.shortfall],
  ] as const;
}

export function OperationStateCard({
  operation,
  title = "Operation progress",
  onRefresh,
  jobsHref,
}: {
  operation: OperationState;
  title?: string;
  onRefresh?: () => void;
  jobsHref?: string;
}) {
  const status = label(operation.status);
  return (
    <section className="rounded-2xl border border-line bg-surface-2/50 p-4" role="status" aria-label={title} aria-live="polite">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-ink">{title}</h2>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px] text-ink-faint">
            {operation.runId ? <span>Run {operation.runId}</span> : null}
            {operation.jobId ? <span>Job {operation.jobId}</span> : null}
          </div>
        </div>
        <span className="pill bg-brand-500/15 text-brand-700 dark:text-brand-300">{status}</span>
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
        <div><dt className="text-ink-faint">Stage</dt><dd className="mt-1 font-medium text-ink">{label(operation.stage)}</dd></div>
        <div><dt className="text-ink-faint">Queue age</dt><dd className="mt-1 font-medium text-ink">{age(operation.queueAgeSeconds)}</dd></div>
        <div><dt className="text-ink-faint">Retries</dt><dd className="mt-1 font-medium text-ink">{operation.retries ?? 0}</dd></div>
        <div><dt className="text-ink-faint">Worker</dt><dd className="mt-1 font-medium text-ink">{label(operation.workerStatus)}</dd></div>
      </dl>
      <div className="mt-4 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4 lg:grid-cols-6">
        {progressRows(operation).map(([name, value]) => <div key={name} className="rounded-lg border border-line bg-surface-3/50 px-2.5 py-2"><div className="text-ink-faint">{name}</div><div className="mt-1 font-semibold text-ink"><DisplayValue value={value} /></div></div>)}
      </div>
      {(operation.blockers?.length || operation.terminalReason || operation.reuse || operation.freshness || operation.sourceLanes) ? <div className="mt-3 space-y-2 text-xs text-ink-muted">
        {operation.blockers?.length ? <div><b className="text-ink">Blockers:</b> {operation.blockers.join(", ")}</div> : null}
        {operation.terminalReason ? <div><b className="text-ink">Terminal reason:</b> {operation.terminalReason}</div> : null}
        {operation.reuse ? <div><b className="text-ink">Reuse:</b> <DisplayValue value={operation.reuse} /></div> : null}
        {operation.freshness ? <div><b className="text-ink">Freshness:</b> <DisplayValue value={operation.freshness} /></div> : null}
        {operation.sourceLanes ? <div><b className="text-ink">Source lanes:</b> <DisplayValue value={operation.sourceLanes} /></div> : null}
      </div> : null}
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-muted">
        <span>Last progress: {operation.lastProgressAt ? new Date(operation.lastProgressAt).toLocaleString() : "not reported"}</span>
        {operation.reasonCode ? <span>Reason: <code>{operation.reasonCode}</code></span> : null}
        {operation.requestId ? <span>Request ID: <code>{operation.requestId}</code></span> : null}
      </div>
      {operation.deadlineReached && onRefresh ? <div className="mt-3"><PollingDeadlineState onRefresh={onRefresh} /></div> : null}
      {jobsHref ? <Link className="mt-3 inline-flex rounded-md border border-line px-2.5 py-1 text-xs font-medium text-ink hover:bg-surface-2" href={jobsHref}>Open jobs monitor</Link> : null}
    </section>
  );
}
