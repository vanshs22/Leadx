"use client";

/**
 * Live-update helpers.
 *
 * Design: Supabase realtime is the primary push channel (see crmRealtime.ts).
 * Polling is only a safety net, and it must be cheap:
 *   - it pauses completely while the tab is hidden (no background traffic),
 *   - it fires once immediately when the tab becomes visible again,
 *   - it never replaces page navigation with a hard reload.
 */
import { useEffect, useRef, useState } from "react";
import { getPortalPollingDeadlineMs } from "./config";

/** Drop-in for setInterval/clearInterval that skips ticks while the tab is hidden. */
export function setVisibleInterval(fn: () => void, ms: number): number {
  const tick = () => {
    if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
    fn();
  };
  const id = window.setInterval(tick, ms);
  const onVisible = () => { if (document.visibilityState === "visible") fn(); };
  document.addEventListener("visibilitychange", onVisible);
  VISIBLE_CLEANUPS.set(id, () => document.removeEventListener("visibilitychange", onVisible));
  return id;
}

const VISIBLE_CLEANUPS = new Map<number, () => void>();

export function clearVisibleInterval(id: number | undefined | null) {
  if (id == null) return;
  window.clearInterval(id);
  VISIBLE_CLEANUPS.get(id)?.();
  VISIBLE_CLEANUPS.delete(id);
}

/**
 * Run `load` on mount, whenever `deps` change, on tab re-focus, and on a slow
 * visibility-aware interval. Keeps the latest `load` in a ref so callers don't
 * need to memoise it.
 */
export function useLiveLoader(
  load: () => void | Promise<void>,
  pollMs = 30_000,
  deps: unknown[] = [],
  options: { deadlineMs?: number; onDeadline?: () => void; runImmediately?: boolean } = {},
) {
  const ref = useRef(load);
  ref.current = load;
  const deadlineRef = useRef(options.onDeadline);
  deadlineRef.current = options.onDeadline;
  const [deadlineReached, setDeadlineReached] = useState(false);
  const deadlineMs = options.deadlineMs ?? getPortalPollingDeadlineMs();
  useEffect(() => {
    let alive = true;
    const startedAt = Date.now();
    let expired = false;
    const run = () => {
      if (!alive || expired) return;
      if (deadlineMs > 0 && Date.now() - startedAt >= deadlineMs) {
        expired = true;
        setDeadlineReached(true);
        deadlineRef.current?.();
        return;
      }
      void Promise.resolve(ref.current()).catch(() => {});
    };
    setDeadlineReached(false);
    if (options.runImmediately !== false) run();
    const id = pollMs > 0 ? setVisibleInterval(run, pollMs) : undefined;
    return () => { alive = false; clearVisibleInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pollMs, deadlineMs, ...deps]);
  return deadlineReached;
}

/** Start a fresh bounded polling window after an explicit user refresh. */
export function useBoundedPolling(
  load: () => void | Promise<void>,
  pollMs = 30_000,
  deps: unknown[] = [],
) {
  return useLiveLoader(load, pollMs, deps);
}


/** Return IDs whose row JSON changed since the previous successful render. */
export function useChangedIds<T>(rows: T[], idOf: (row: T) => string): Set<string> {
  const previous = useRef<Map<string, string>>(new Map());
  const [changed, setChanged] = useState<Set<string>>(new Set());
  useEffect(() => {
    const next = new Map<string, string>();
    const ids = new Set<string>();
    for (const row of rows || []) {
      const id = String(idOf(row));
      const sig = JSON.stringify(row);
      next.set(id, sig);
      if (previous.current.has(id) && previous.current.get(id) !== sig) ids.add(id);
    }
    previous.current = next;
    if (!ids.size) return;
    setChanged(ids);
    const timer = window.setTimeout(() => setChanged(new Set()), 1800);
    return () => window.clearTimeout(timer);
  }, [rows, idOf]);
  return changed;
}

export function useFlashOnChange<T>(value: T): boolean {
  const previous = useRef<T>(value);
  const [changed, setChanged] = useState(false);
  useEffect(() => {
    if (Object.is(previous.current, value)) return;
    previous.current = value;
    setChanged(true);
    const timer = window.setTimeout(() => setChanged(false), 1800);
    return () => window.clearTimeout(timer);
  }, [value]);
  return changed;
}
