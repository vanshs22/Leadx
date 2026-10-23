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
    <div role="status" className="rounded-lg border border-amber-300/60 bg-amber-50/60 px-4 py-3 text-sm text-ink dark:bg-transparent">
      <p className="font-medium">Live updates paused</p>
      <p className="mt-1 text-xs text-ink-muted">Refresh to check for the latest workspace state.</p>
      <button aria-label="Refresh" type="button" onClick={onRefresh} className="mt-3 rounded-md border border-line px-2.5 py-1 text-xs font-medium text-ink hover:bg-surface-2">Refresh</button>
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
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      {icon ? <div className="mb-1 text-ink-faint">{icon}</div> : null}
      <p className="text-sm font-medium text-ink">{title}</p>
      {hint ? <p className="max-w-sm text-xs text-ink-muted">{hint}</p> : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}

/** Skeleton rows sized to a table, so layout doesn't jump when data lands. */
export function LoadingRows({ rows = 5, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="animate-pulse divide-y divide-line" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading…</span>
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex gap-4 px-4 py-3">
          {Array.from({ length: cols }).map((_, c) => (
            <div
              key={c}
              className="h-3 rounded bg-surface-3"
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
  return (
    <div role="alert" className="mx-4 my-6 rounded-lg border border-rust-300/60 bg-rust-50/60 px-4 py-3 dark:bg-transparent">
      <p className="text-sm font-medium text-ink">Couldn&apos;t load this</p>
      <p className="mt-1 break-words text-xs text-ink-muted">{details.message}</p>
      {details.requestId ? <p className="mt-2 text-[11px] text-ink-faint">Request ID: <code className="font-mono">{details.requestId}</code></p> : null}
      {onRetry && (details.retryable || details.action === "refresh" || details.action === "sign_in") ? (
        <button aria-label={actionLabel}
          type="button"
          onClick={onRetry}
          className="mt-3 rounded-md border border-line px-2.5 py-1 text-xs font-medium text-ink hover:bg-surface-2"
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
