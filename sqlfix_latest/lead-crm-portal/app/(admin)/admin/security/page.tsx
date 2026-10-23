"use client";

import { useLiveLoader } from "@/lib/live";
import { useMemo, useState } from "react";
import { admin } from "@/lib/admin";
import { EmptyState, ErrorState, LoadingRows, PollingDeadlineState } from "@/components/ui/States";
import { FreshnessNote } from "@/components/ui/SurfaceMeta";
import { PageHeader } from "@/components/ui/Primitives";

export default function SecurityPage() {
  const [audit, setAudit] = useState<any[]>([]);
  const [security, setSecurity] = useState<any[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [severity, setSeverity] = useState("");
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [pollingExpired, setPollingExpired] = useState(false);
  const [pollingReset, setPollingReset] = useState(0);

  async function load() {
    setLoading(true); setError(null);
    try {
      const [a, s] = await Promise.all([admin.auditLog(100), admin.securityEvents(100)]);
      setAudit(a.events || []); setSecurity(s.events || []); setLastUpdated(new Date().toLocaleTimeString());
    } catch (caught) {
      setError(caught);
    } finally {
      setLoading(false);
    }
  }

  const refresh = () => {
    setPollingExpired(false);
    setPollingReset((value) => value + 1);
    void load();
  };

  useLiveLoader(load, 30_000, [pollingReset], {
    onDeadline: () => setPollingExpired(true),
  });

  const needle = query.trim().toLowerCase();
  const filteredSecurity = useMemo(() => security.filter((x) => (!severity || x.severity === severity) && (!needle || `${x.event_type} ${x.severity} ${x.created_at}`.toLowerCase().includes(needle))), [security, severity, needle]);
  const filteredAudit = useMemo(() => audit.filter((x) => !needle || `${x.action} ${x.actor_type} ${x.resource_type} ${x.resource_id} ${x.reason}`.toLowerCase().includes(needle)), [audit, needle]);

  function exportAudit() {
    const rows = filteredAudit.map((x) => ({ action: x.action, actor_type: x.actor_type, resource_type: x.resource_type || "", resource_id: x.resource_id || "", reason: x.reason || "", created_at: x.created_at || "" }));
    if (!rows.length) return;
    const csv = [Object.keys(rows[0]).join(","), ...rows.map((row) => Object.values(row).map((value) => JSON.stringify(value)).join(","))].join("\n");
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); a.download = "leadx-audit-log.csv"; a.click(); URL.revokeObjectURL(a.href);
  }

  if (loading) return <div className="page"><h1 className="page-title">Audit center</h1><LoadingRows rows={6} cols={4} /></div>;
  if (error) return <div className="page"><ErrorState error={error} onRetry={refresh} /></div>;

  return <div className="page space-y-5"><PageHeader eyebrow="Security & governance" title="Audit center" description="Administrative actions, security signals, and compliance evidence from the latest API window." actions={<div className="flex gap-2"><button type="button" aria-label="Refresh security and audit events" className="btn-secondary" onClick={refresh}>Refresh</button><button type="button" aria-label="Export filtered audit events" className="btn-secondary" onClick={exportAudit} disabled={!filteredAudit.length}>Export audit</button></div>} meta={<FreshnessNote label="Control-plane snapshot" detail={lastUpdated ? `updated ${lastUpdated}` : "updated unavailable"} />} />
    {pollingExpired && <PollingDeadlineState onRefresh={refresh} />}
    <div className="card-pad flex flex-wrap gap-3"><input className="input min-w-[220px] flex-1" placeholder="Search event, actor, resource…" value={query} onChange={(e) => setQuery(e.target.value)} /><select className="input max-w-[180px]" value={severity} onChange={(e) => setSeverity(e.target.value)}><option value="">All severities</option>{Array.from(new Set(security.map((x) => x.severity).filter(Boolean))).map((value) => <option key={value} value={value}>{value}</option>)}</select><span className="self-center text-xs text-ink-faint">{filteredSecurity.length} security · {filteredAudit.length} audit matches</span></div>
    <section className="data-card p-5"><div className="flex items-center justify-between gap-3"><div><div className="section-kicker">Security events</div><p className="mt-1 text-xs text-ink-faint">Signals returned by the security-events API.</p></div><span className="pill bg-surface-3">{filteredSecurity.length}</span></div><div className="mt-3 space-y-2">{filteredSecurity.map((x) => <div key={x.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-surface-2 p-3 text-sm"><div><strong>{x.event_type || "Event type unavailable"}</strong><div className="text-xs text-ink-faint">{x.created_at || "Timestamp unavailable"}</div></div><span className="pill bg-surface-3">{x.severity || "severity unavailable"}</span></div>)}{!filteredSecurity.length && <EmptyState title="No security events match" hint={query || severity ? "Clear the filters to inspect the loaded window." : "No security events were returned."} />}</div></section>
    <section className="data-card p-5"><div className="flex items-center justify-between gap-3"><div><div className="section-kicker">Audit trail</div><p className="mt-1 text-xs text-ink-faint">Actions are shown only when returned by the audit API.</p></div><span className="pill bg-surface-3">{filteredAudit.length}</span></div><div className="mt-3 space-y-2">{filteredAudit.map((x) => <div key={x.id} className="rounded-xl border border-line bg-surface-2 p-3"><div className="flex justify-between gap-3 text-sm"><strong>{x.action || "Action unavailable"}</strong><span className="text-xs text-ink-faint">{x.created_at || "Timestamp unavailable"}</span></div><div className="mt-1 text-xs text-ink-muted">{x.actor_type || "Actor unavailable"} · {x.resource_type || "system"} {x.resource_id || ""}</div>{x.reason && <div className="mt-1 text-xs text-ink-faint">Reason: {x.reason}</div>}</div>)}{!filteredAudit.length && <EmptyState title="No audit entries match" hint={query ? "Try a different actor, resource, or action." : "No audit entries were returned."} />}</div></section>
  </div>;
}
