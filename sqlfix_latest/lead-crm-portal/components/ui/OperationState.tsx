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
  crmSaved?: number;
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
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const entries = Object.entries(value as Record<string, unknown>);
      return entries.length ? Object.fromEntries(entries) : undefined;
    }
    return undefined;
  };
  const listValue = (...values: unknown[]) => {
    const value = first(...values);
    if (Array.isArray(value)) return value.map((item) => {
      if (item && typeof item === "object") return Object.entries(item as Record<string, unknown>).map(([key, count]) => `${key}: ${String(count)}`).join(", ");
      return String(item);
    }).filter(Boolean);
    if (value && typeof value === "object") return Object.entries(value as Record<string, unknown>).map(([key, count]) => `${key}: ${String(count)}`);
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
    crmSaved: number(first(source.crm_saved, source.crmSaved, progress.crm_saved, run.crm_saved, request.crm_saved_count)),
    target: number(first(source.target, source.requested_target, progress.target, run.requested_target, request.requested_target, request.requested_leads)),
    effectiveTarget: number(first(source.effective_target, progress.effective_target, run.effective_target, run.target_assigned, request.effective_target, request.approved_leads)),
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
    ["Assigned", operation.assigned], ["CRM saved", operation.crmSaved], ["Delivered", operation.delivered],
    ["Target", operation.target], ["Effective target", operation.effectiveTarget], ["Shortfall", operation.shortfall],
  ] as const;
}

type OperationTone = "success" | "danger" | "warn" | "neutral" | "brand";

function operationTone(status: string): OperationTone {
  const normalized = status.toLowerCase();
  if (["completed", "complete", "done", "delivered", "approved", "active"].includes(normalized)) return "success";
  if (["failed", "error", "rejected", "lost"].includes(normalized)) return "danger";
  if (["cancelled", "canceled"].includes(normalized)) return "neutral";
  if (["partial", "degraded"].includes(normalized)) return "warn";
  return "brand";
}

const TONE_BADGE: Record<OperationTone, string> = {
  success: "badge-success",
  danger: "badge-danger",
  warn: "badge-warn",
  neutral: "badge-neutral",
  brand: "badge-brand",
};

const TONE_DOT: Record<OperationTone, string> = {
  success: "bg-emerald-600 dark:bg-emerald-400",
  danger: "bg-rust-500",
  warn: "bg-amber-500",
  neutral: "bg-line-strong",
  brand: "bg-brand-600 dark:bg-brand-300",
};

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
  const tone = operationTone(operation.status);
  const hasDetails = Boolean(operation.blockers?.length || operation.terminalReason || operation.reuse || operation.freshness || operation.sourceLanes);
  return (
    <section className="overflow-hidden rounded-xl border border-line bg-surface-2 shadow-card" role="status" aria-label={title} aria-live="polite">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <h2 className="section-title">{title}</h2>
          {operation.runId || operation.jobId ? (
            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-ink-faint">
              {operation.runId ? <span>Run <span className="font-mono text-xs text-ink-muted">{operation.runId}</span></span> : null}
              {operation.jobId ? <span>Job <span className="font-mono text-xs text-ink-muted">{operation.jobId}</span></span> : null}
            </div>
          ) : null}
        </div>
        <span className={`${TONE_BADGE[tone]} capitalize`}><span className={`h-1.5 w-1.5 rounded-full ${TONE_DOT[tone]}`} aria-hidden="true" />{status}</span>
      </div>
      <div className="p-4">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
          <div><dt className="label">Stage</dt><dd className="mt-1 text-[0.8125rem] font-medium capitalize text-ink">{label(operation.stage)}</dd></div>
          <div><dt className="label">Queue age</dt><dd className="mt-1 text-[0.8125rem] font-medium tabular-nums text-ink">{age(operation.queueAgeSeconds)}</dd></div>
          <div><dt className="label">Retries</dt><dd className="mt-1 text-[0.8125rem] font-medium tabular-nums text-ink">{operation.retries ?? 0}</dd></div>
          <div><dt className="label">Worker</dt><dd className="mt-1 text-[0.8125rem] font-medium capitalize text-ink">{label(operation.workerStatus)}</dd></div>
        </dl>
        <div className="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-4 lg:grid-cols-6">
          {progressRows(operation).map(([name, value]) => <div key={name} className="bg-surface-2 px-3 py-2.5"><div className="text-[11px] text-ink-faint">{name}</div><div className="mt-1 text-sm font-semibold tabular-nums text-ink"><DisplayValue value={value} /></div></div>)}
        </div>
        {hasDetails ? <div className="mt-4 space-y-1.5 rounded-lg border border-line bg-surface-3/60 px-3 py-2.5 text-xs text-ink-muted">
          {operation.blockers?.length ? <div><b className="font-semibold text-ink">Blockers:</b> {operation.blockers.join(", ")}</div> : null}
          {operation.terminalReason ? <div><b className="font-semibold text-ink">Terminal reason:</b> {operation.terminalReason}</div> : null}
          {operation.reuse ? <div className="break-words"><b className="font-semibold text-ink">Reuse:</b> <DisplayValue value={operation.reuse} /></div> : null}
          {operation.freshness ? <div className="break-words"><b className="font-semibold text-ink">Freshness:</b> <DisplayValue value={operation.freshness} /></div> : null}
          {operation.sourceLanes ? <div className="break-words"><b className="font-semibold text-ink">Source lanes:</b> <DisplayValue value={operation.sourceLanes} /></div> : null}
        </div> : null}
        {operation.deadlineReached && onRefresh ? <div className="mt-4"><PollingDeadlineState onRefresh={onRefresh} /></div> : null}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-line bg-surface-3/50 px-4 py-2.5 text-xs text-ink-muted">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <span>Last progress: <span className="tabular-nums">{operation.lastProgressAt ? new Date(operation.lastProgressAt).toLocaleString() : "not reported"}</span></span>
          {operation.reasonCode ? <span>Reason: <code className="font-mono text-xs">{operation.reasonCode}</code></span> : null}
          {operation.requestId ? <span>Request ID: <code className="font-mono text-xs">{operation.requestId}</code></span> : null}
        </div>
        {jobsHref ? <Link className="btn-secondary btn-sm" href={jobsHref}>Open jobs monitor</Link> : null}
      </div>
    </section>
  );
}
