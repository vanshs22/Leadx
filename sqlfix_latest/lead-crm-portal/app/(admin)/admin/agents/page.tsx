"use client";

import { useCallback, useEffect, useState } from "react";
import { admin } from "@/lib/admin";
import { PageHeader, Panel, StatCard, StatGrid, StatusBadge } from "@/components/ui/Primitives";
import { DataState, EmptyState, ErrorState, LoadingRows } from "@/components/ui/States";
import { Icon } from "@/components/ui/Icon";

export default function AdminAgentsPage() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try { setData(await admin.platformAgents()); } catch (caught) { setError(caught); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const agents: any[] = data?.agents || [];
  const runs: any[] = data?.runs || [];
  const active = runs.filter((run) => ["queued", "running", "started"].includes(String(run.status))).length;
  const failed = runs.filter((run) => ["failed", "error"].includes(String(run.status))).length;
  return <div className="page">
    <PageHeader eyebrow="Lead engine" title="Agent operations" description="Platform-wide registry, execution health, and coordinated GTM mission coverage." actions={<button type="button" className="btn-secondary" onClick={() => void load()}><Icon name="refresh" size={14} />Refresh</button>} />
    <StatGrid cols={4}>
      <StatCard label="Registered agents" value={agents.length || "—"} tone="brand" icon={<Icon name="spark" size={16} />} />
      <StatCard label="Recent runs" value={runs.length || "—"} tone="neutral" icon={<Icon name="run" size={16} />} />
      <StatCard label="Active now" value={active} tone="accent" icon={<Icon name="clock" size={16} />} />
      <StatCard label="Recent failures" value={failed} tone={failed ? "danger" : "success"} icon={<Icon name="health" size={16} />} />
    </StatGrid>
    <div className="grid gap-4 lg:grid-cols-3">
      <Panel title="31-agent registry" description="One platform registry. Client workspaces do not expose this control surface." className="lg:col-span-2" bodyClassName="p-0">
        <DataState loading={loading} error={error} onRetry={() => void load()} isEmpty={!agents.length} empty={<EmptyState title="Agent registry unavailable" hint="The platform registry could not be loaded." />} skeleton={<LoadingRows rows={8} cols={3} />}>
          <div className="divide-y divide-line">{agents.map((agent) => <article key={agent.id} className="px-5 py-3.5"><div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><div className="flex items-center gap-2"><h3 className="text-[0.875rem] font-semibold text-ink">{agent.name || agent.id}</h3><span className="badge-neutral font-mono">{agent.id}</span>{agent.needs_approval ? <span className="badge-accent">Approval</span> : null}</div><p className="mt-1 text-xs leading-5 text-ink-muted">{agent.description}</p></div><span className="text-xs tabular-nums text-ink-faint">max {agent.max_duration_seconds ?? "—"}s</span></div></article>)}</div>
        </DataState>
      </Panel>
      <Panel title="Recent executions" description="Tenant-scoped runs, monitored by operators." bodyClassName="p-0">
        {error ? <div className="p-4"><ErrorState error={error} onRetry={() => void load()} /></div> : runs.length === 0 ? <EmptyState title="No agent runs yet" /> : <ul className="divide-y divide-line">{runs.slice(0, 12).map((run) => <li key={run.id} className="px-5 py-3"><div className="flex items-center justify-between gap-2"><span className="font-mono text-[11px] text-ink-muted">{String(run.agent_id || "agent")}</span><StatusBadge status={run.status} /></div><div className="mt-1 truncate font-mono text-[10px] text-ink-faint" title={run.tenant_id}>{run.tenant_id || "tenant unavailable"}</div><div className="mt-1 text-[11px] tabular-nums text-ink-faint">{run.created_at ? new Date(run.created_at).toLocaleString() : "Time unavailable"}</div></li>)}</ul>}
      </Panel>
    </div>
  </div>;
}
