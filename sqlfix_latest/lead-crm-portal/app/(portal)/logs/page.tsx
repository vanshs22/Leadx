"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { crmLogs, type WorkspaceLog } from "@/lib/api";
import { useCrmChange } from "@/lib/crmRealtime";
import { getTenantId } from "@/lib/tenant";
import { Badge, DataTable, FilterBar, PageHeader, StatusBadge } from "@/components/ui/Primitives";
import { DataState, EmptyState, LoadingRows } from "@/components/ui/States";
import { Icon } from "@/components/ui/Icon";

function prettyAction(value: string) { return value.replace(/[._-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()); }

/** Presentation-only level derived from the action name (the API has no level field). */
function logLevel(action: string): { status: string; label: string } {
  const a = String(action || "").toLowerCase();
  if (/(fail|error|denied|reject|bounce)/.test(a)) return { status: "error", label: "Error" };
  if (/(warn|retry|pause|stale|degrad)/.test(a)) return { status: "warning", label: "Warn" };
  return { status: "info", label: "Info" };
}

export default function LogsPage() {
  const tenantId = getTenantId();
  const [logs, setLogs] = useState<WorkspaceLog[]>([]), [before, setBefore] = useState<string | null>(null), [search, setSearch] = useState(""), [action, setAction] = useState(""), [loading, setLoading] = useState(true), [loadingMore, setLoadingMore] = useState(false), [error, setError] = useState<unknown>(null);
  const load = useCallback(async (reset = false) => { if (!tenantId) return; reset ? setLoading(true) : setLoadingMore(true); setError(null); try { const result = await crmLogs.list(tenantId, { before: reset ? undefined : before || undefined, search: search.trim() || undefined, action: action || undefined, limit: 50 }); setLogs((current) => reset ? result.logs : [...current, ...result.logs.filter((next) => !current.some((old) => old.id === next.id))]); setBefore(result.next_before); } catch (caught) { setError(caught); } finally { setLoading(false); setLoadingMore(false); } }, [tenantId, before, search, action]);
  useEffect(() => { void load(true); }, [tenantId, search, action]);
  useCrmChange(() => { void load(true); }, { tables: ["leadx_event_log", "audit_log", "lead_activities", "activities"] });
  const actionOptions = useMemo(() => Array.from(new Set(logs.map((l) => l.action).filter(Boolean))).slice(0, 30), [logs]);

  return (
    <div className="page">
      <PageHeader
        eyebrow="Workspace history"
        title="Logs"
        description="Live activity and workspace events. Older rows load only when requested."
        meta={<><span className="inline-flex items-center gap-1.5 font-medium text-ink-muted"><span className="live-dot" aria-hidden="true" />Live</span><span className="tabular-nums">{logs.length} events loaded</span></>}
      />
      <FilterBar onClear={() => { setSearch(""); setAction(""); }}>
        <label className="sr-only" htmlFor="log-search">Search logs</label>
        <div className="relative min-w-[220px] flex-1">
          <Icon name="search" size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint" aria-hidden="true" />
          <input id="log-search" type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search events…" className="input pl-8" />
        </div>
        <label className="sr-only" htmlFor="log-action">Filter by action</label>
        <select id="log-action" value={action} onChange={(e) => setAction(e.target.value)} className="select md:w-56"><option value="">All actions</option>{actionOptions.map((item) => <option key={item} value={item}>{prettyAction(item)}</option>)}</select>
      </FilterBar>
      <DataState
        loading={loading && !logs.length}
        error={error}
        isEmpty={!logs.length}
        empty={<div className="card"><EmptyState title="No workspace events match these filters" hint="Clear the filters or wait for new activity to stream in." /></div>}
        onRetry={() => void load(true)}
        skeleton={<div className="card"><LoadingRows rows={8} cols={5} /></div>}
      >
        <DataTable caption="Workspace event logs">
          <thead>
            <tr>
              <th className="w-[180px]">Timestamp</th>
              <th className="w-[90px]">Level</th>
              <th>Message</th>
              <th>Source</th>
              <th className="w-[140px]">Request id</th>
            </tr>
          </thead>
          <tbody>
            {logs.map((log) => {
              const level = logLevel(log.action);
              return (
                <tr key={log.id}>
                  <td className="whitespace-nowrap"><time className="font-mono text-xs tabular-nums text-ink-muted" dateTime={log.created_at}>{new Date(log.created_at).toLocaleString()}</time></td>
                  <td><StatusBadge status={level.status} label={level.label} /></td>
                  <td className="max-w-[460px]">
                    <div className="truncate font-medium text-ink">{prettyAction(log.action)}</div>
                    <div className="truncate text-xs text-ink-muted" title={log.summary}>{log.summary}</div>
                  </td>
                  <td className="whitespace-nowrap text-xs text-ink-muted"><Badge tone="neutral">{log.actor_type || "system"}</Badge>{log.resource_type ? <span className="ml-1.5 text-ink-faint">{log.resource_type}</span> : null}</td>
                  <td><code className="block max-w-[140px] truncate font-mono text-xs text-ink-faint" title={log.resource_id || log.id}>{log.resource_id || log.id}</code></td>
                </tr>
              );
            })}
          </tbody>
        </DataTable>
      </DataState>
      {before && !loading ? (
        <div className="flex items-center justify-between gap-3 text-xs text-ink-faint">
          <span>Older events are fetched with the next_before cursor.</span>
          <button type="button" className="btn-secondary btn-sm" aria-label="Load older logs" disabled={loadingMore} onClick={() => { if (!loadingMore) void load(false); }}>
            <Icon name="download" size={14} aria-hidden="true" />{loadingMore ? "Loading older logs…" : "Load older logs"}
          </button>
        </div>
      ) : null}
      {loadingMore && !before ? <p className="text-center text-xs text-ink-faint" aria-live="polite">Loading older logs…</p> : null}
    </div>
  );
}
