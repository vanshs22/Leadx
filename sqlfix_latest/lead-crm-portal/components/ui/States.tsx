/**
 * Shared empty / loading / error states.
 *
 * Every table and panel in the portal was rolling its own "no rows" div and
 * its own spinner, and several just rendered nothing at all — which is how a
 * failed fetch ended up looking identical to an empty result set. One set of
 * primitives so the three states are always visually distinct.
 */
import type { ReactNode } from "react";
import { ApiClientError } from "@/lib/api";

export type PortalRequestState<T> =
  | { status: "loading"; data: null }
  | { status: "success"; data: T }
  | { status: "empty"; data: T }
  | { status: "error"; data: null; error: unknown };

export function portalErrorDetails(error: unknown): {
  message: string;
  requestId?: string;
  retryable: boolean;
  action: "sign_in" | "retry" | "refresh" | "none";
} {
  if (error instanceof ApiClientError) {
    return {
      message: error.message,
      requestId: error.requestId || undefined,
      retryable: error.recovery.retryable,
      action: error.recovery.action,
    };
  }
  return {
    message: error instanceof Error ? error.message : typeof error === "string" ? error : "Something went wrong.",
    retryable: true,
    action: "retry",
  };
}

export function PollingDeadlineState({ onRefresh }: { onRefresh: () => void }) {
  return (
    <div role="status" className="notice notice-warning items-center">
      <div className="min-w-0">
        <p className="font-semibold">Automatic safety refresh window ended</p>
        <p className="mt-0.5 text-xs opacity-80">Live push updates remain enabled. Refresh to reconcile the latest authoritative state.</p>
      </div>
      <button aria-label="Refresh" type="button" onClick={onRefresh} className="btn-secondary btn-sm shrink-0">Refresh</button>
    </div>
  );
}

export function EmptyState({
  title,
  hint,
  action,
  icon,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-1.5 px-6 py-12 text-center">
      <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-full border border-line bg-surface-3 text-ink-faint" aria-hidden="true">
        {icon ?? <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 7h16v12H4zM4 7l2-3h12l2 3M9 12h6" /></svg>}
      </div>
      <p className="text-[0.875rem] font-semibold text-ink">{title}</p>
      {hint ? <p className="max-w-sm text-[0.8125rem] leading-5 text-ink-muted">{hint}</p> : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}

/** Skeleton rows sized to a table, so layout doesn't jump when data lands. */
export function LoadingRows({ rows = 5, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="divide-y divide-line" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading…</span>
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex gap-4 px-4 py-3.5">
          {Array.from({ length: cols }).map((_, c) => (
            <div
              key={c}
              className="skeleton h-3"
              style={{ width: c === 0 ? "28%" : `${Math.max(10, 22 - c * 3)}%` }}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  const details = portalErrorDetails(error);
  const actionLabel = details.action === "sign_in" ? "Sign in again" : details.action === "refresh" ? "Refresh" : "Retry";
  const message = details.message;
  const unavailable = details.retryable && details.action === "retry";
  return (
    <div role="alert" className={`notice ${unavailable ? "notice-warning" : "notice-danger"} my-1 flex-col !items-start !justify-start gap-0`}>
      <p className="font-semibold">{unavailable ? "LeadX is reconnecting" : "Couldn&apos;t load this"}</p>
      <p className="mt-0.5 break-words text-xs opacity-85">{message}</p>
      {details.requestId ? <p className="mt-2 text-[11px] opacity-70">Request ID: <code className="font-mono">{details.requestId}</code></p> : null}
      {onRetry && (details.retryable || details.action === "refresh" || details.action === "sign_in") ? (
        <button aria-label={actionLabel}
          type="button"
          onClick={onRetry}
          className="btn-secondary btn-sm mt-3"
        >
          {actionLabel}
        </button>
      ) : null}
    </div>
  );
}

/**
 * The whole decision tree in one call, so a page can't accidentally render an
 * empty state while a request is still in flight or has failed.
 */
export function DataState({
  loading,
  error,
  isEmpty,
  empty,
  onRetry,
  children,
  skeleton,
}: {
  loading: boolean;
  error?: unknown;
  isEmpty?: boolean;
  empty?: ReactNode;
  onRetry?: () => void;
  children: ReactNode;
  skeleton?: ReactNode;
}) {
  if (loading) return <>{skeleton ?? <LoadingRows />}</>;
  if (error) return <ErrorState error={error} onRetry={onRetry} />;
  if (isEmpty) return <>{empty ?? <EmptyState title="Nothing here yet" />}</>;
  return <>{children}</>;
}
