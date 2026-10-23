"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { crmLogs, type WorkspaceLog } from "@/lib/api";
import { useCrmChange } from "@/lib/crmRealtime";
import { getTenantId } from "@/lib/tenant";

function prettyAction(value: string) {
  return value.replace(/[._-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function LogsPage() {
  const tenantId = getTenantId();
  const [logs, setLogs] = useState<WorkspaceLog[]>([]);
  const [before, setBefore] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [action, setAction] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (reset = false) => {
    if (!tenantId) return;
    reset ? setLoading(true) : setLoadingMore(true);
    setError("");
    try {
      const result = await crmLogs.list(tenantId, {
        before: reset ? undefined : before || undefined,
        search: search.trim() || undefined,
        action: action || undefined,
        limit: 50,
      });
      setLogs((current) => reset ? result.logs : [...current, ...result.logs.filter((next) => !current.some((old) => old.id === next.id))]);
      setBefore(result.next_before);
    } catch (e: any) {
      setError(e?.message || "Could not load logs");
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, [tenantId, before, search, action]);

  useEffect(() => { void load(true); }, [tenantId, search, action]);
  useCrmChange(() => { void load(true); }, { tables: ["leadx_event_log", "audit_log", "lead_activities", "activities"] });

  const actionOptions = useMemo(() => {
    const set = new Set(logs.map((l) => l.action).filter(Boolean));
    return Array.from(set).slice(0, 30);
  }, [logs]);

  return (
    <div className="page">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="eyebrow">Workspace history</div>
          <h1 className="page-title mt-1">Logs</h1>
          <p className="page-sub">Live activity and workspace events. Logs are paged and loaded only when you ask for them.</p>
        </div>
        <span className="pill bg-signal-50 text-signal-700 dark:bg-signal-500/10 dark:text-signal-300">● Live</span>
      </div>

      <div className="data-card mb-4 p-3">
        <div className="flex flex-col gap-2 md:flex-row">
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search events…" className="input flex-1" />
          <select value={action} onChange={(e) => setAction(e.target.value)} className="input md:w-56">
            <option value="">All actions</option>
            {actionOptions.map((item) => <option key={item} value={item}>{prettyAction(item)}</option>)}
          </select>
        </div>
      </div>

      {error && <div className="mb-4 rounded-xl border border-rust-200 bg-rust-50 px-4 py-3 text-sm text-rust-700 dark:border-rust-500/20 dark:bg-rust-500/10 dark:text-rust-300">{error}</div>}

      <div className="data-card overflow-hidden">
        <div className="hidden grid-cols-[150px_150px_1fr_180px] gap-3 border-b border-line bg-surface-2 px-4 py-3 text-[10px] font-bold uppercase tracking-wider text-ink-faint md:grid">
          <span>When</span><span>Action</span><span>Event</span><span>Source</span>
        </div>
        {loading && !logs.length ? (
          <div className="space-y-2 p-4">{Array.from({ length: 8 }).map((_, i) => <div key={i} className="skeleton h-12 w-full" />)}</div>
        ) : !logs.length ? (
          <div className="p-10 text-center text-sm text-ink-muted">No workspace events match these filters.</div>
        ) : (
          <div className="divide-y divide-line">
            {logs.map((log) => (
              <div key={log.id} className="grid gap-2 px-4 py-3 text-sm md:grid-cols-[150px_150px_1fr_180px] md:items-center">
                <time className="text-xs text-ink-faint" dateTime={log.created_at}>{new Date(log.created_at).toLocaleString()}</time>
                <span className="font-medium text-ink">{prettyAction(log.action)}</span>
                <span className="min-w-0 truncate text-ink-muted">{log.summary}</span>
                <span className="text-xs text-ink-faint">{log.actor_type}{log.resource_type ? ` · ${log.resource_type}` : ""}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {before && !loading && <div className="mt-4 text-center"><button aria-label="Action" className="btn-secondary" disabled={loadingMore} onClick={() => void load(false)}>{loadingMore ? "Loading…" : "Load older logs"}</button></div>}
    </div>
  );
}
