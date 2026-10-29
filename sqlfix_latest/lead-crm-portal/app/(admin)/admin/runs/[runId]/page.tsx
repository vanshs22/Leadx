"use client";

import { useCallback, useMemo, useState } from "react";
import { useLiveLoader } from "@/lib/live";
import { useCrmRealtime } from "@/lib/crmRealtime";
import { PollingDeadlineState } from "@/components/ui/States";
import Link from "next/link";
import { useParams } from "next/navigation";
import { admin, adminExtras } from "@/lib/admin";
import { PageHeader, Notice } from "@/components/ui/Primitives";
import { ErrorState } from "@/components/ui/States";
import { OperationStateCard, toOperationState } from "@/components/ui/OperationState";

type RunLead = {
  id?: string;
  lead_id: string;
  run_id: string;
  stage: string;
  tier?: string | null;
  included?: boolean;
  score?: number;
  name?: string;
  phone?: string;
  website?: string;
  city?: string;
  source?: string;
};

const COLUMNS: { key: string; title: string; hint: string }[] = [
  { key: "discovered", title: "Discovered", hint: "All found leads" },
  { key: "enriched", title: "Enriched", hint: "Website/data pulled" },
  { key: "gated", title: "Passed gate", hint: "Quality OK" },
  { key: "assigned", title: "Assigned · Tier A", hint: "On client leads (A)" },
  { key: "assigned_B", title: "Assigned · Tier B", hint: "On client leads (B)" },
  { key: "excluded", title: "Excluded", hint: "Removed from client" },
];

function columnFor(L: RunLead): string {
  if (L.stage === "excluded" || L.included === false) return "excluded";
  if (L.stage === "assigned") {
    return (L.tier || "").toUpperCase() === "B" ? "assigned_B" : "assigned";
  }
  if (L.stage === "gated") return "gated";
  if (L.stage === "enriched") return "enriched";
  return "discovered";
}

function LeadCard({
  lead,
  onDragStart,
}: {
  lead: RunLead;
  onDragStart: (e: React.DragEvent, lead: RunLead) => void;
}) {
  return (
    <div
      draggable
      onDragStart={(e) => onDragStart(e, lead)}
      className="cursor-grab rounded-lg border border-line bg-surface-2 shadow-sm p-3 active:cursor-grabbing hover:border-brand-500/50"
    >
      <div className="text-sm font-medium text-ink line-clamp-2">
        {lead.name || "Unnamed"}
      </div>
      <div className="mt-1 text-xs tabular-nums text-ink-muted">
        {lead.phone || "—"}
      </div>
      <div className="mt-0.5 text-[11px] text-ink-faint line-clamp-1">
        {[lead.city, lead.source].filter(Boolean).join(" · ") || "—"}
      </div>
      {lead.website && (
        <a
          href={lead.website.startsWith("http") ? lead.website : `https://${lead.website}`}
          target="_blank"
          rel="noreferrer"
          className="mt-1 block truncate text-[11px] text-brand-600 dark:text-brand-400/80 hover:underline"
          onClick={(e) => e.stopPropagation()}
        >
          {lead.website.replace(/^https?:\/\//, "")}
        </a>
      )}
      <div className="mt-2 flex items-center justify-between text-[10px] text-ink-faint">
        <span>{lead.tier ? `Tier ${lead.tier}` : "No tier"}</span>
        {lead.score != null && <span>Score {lead.score}</span>}
      </div>
    </div>
  );
}

export default function RunBoardPage() {
  const params = useParams();
  const runId = String(params?.runId || "");
  const [run, setRun] = useState<any>(null);
  const [leads, setLeads] = useState<RunLead[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [msg, setMsg] = useState("");
  const [pollingExpired, setPollingExpired] = useState(false);
  const [pollingReset, setPollingReset] = useState(0);

  const load = useCallback(async () => {
    if (!runId) return;
    try {
      const d: any = await admin.runLeads(runId);
      setRun(d.run);
      setLeads(d.leads || []);
      setError(d.error || "");
    } catch (caught: any) {
      setError(caught?.message || "Could not load run board");
    } finally {
      setLoading(false);
    }
  }, [runId]);

  const runStatus = String(run?.status || "").toLowerCase();
  const runTerminal = ["completed", "complete", "done", "failed", "cancelled", "partial"].includes(runStatus);
  const refresh = () => {
    setPollingExpired(false);
    setPollingReset((value) => value + 1);
    void load();
  };

  useLiveLoader(load, runTerminal ? 0 : 1_500, [runId, runTerminal, pollingReset], {
    onDeadline: () => setPollingExpired(true),
  });
  // Realtime is an invalidation hint only; load() always replaces the board
  // with the tenant-authorized run/lead projection.
  useCrmRealtime(() => { void load(); }, String(run?.tenant_id || ""));

  const buckets = useMemo(() => {
    const map: Record<string, RunLead[]> = {};
    COLUMNS.forEach((c) => (map[c.key] = []));
    for (const L of leads) {
      const k = columnFor(L);
      if (!map[k]) map[k] = [];
      map[k].push(L);
    }
    return map;
  }, [leads]);

  const target = Number(run?.meta?.effective_target ?? run?.meta?.target_assignments ?? run?.requested_target ?? 0);
  const assigned = Number(run?.assigned ?? 0);
  const delivered = Number(run?.delivered ?? 0);
  const targetReached = target > 0 && delivered >= target;
  const operation = run ? toOperationState(run, { runId, deadlineReached: pollingExpired }) : null;
  const blockers = Object.entries((run?.meta?.assignment_blockers || {}) as Record<string, number>);

  async function stopRun() {
    if (!runId || !window.confirm("Stop this pipeline run? New provider work will be fenced and the run will be cancelled.")) return;
    setStopping(true);
    setMsg("");
    try {
      await adminExtras.stopRun(runId);
      setMsg("Run stop requested. Refreshing authoritative status…");
      await load();
    } catch (caught: any) {
      setMsg(caught?.message || "Run could not be stopped");
    } finally {
      setStopping(false);
    }
  }

  function onDragStart(e: React.DragEvent, lead: RunLead) {
    e.dataTransfer.setData("text/lead_id", lead.lead_id);
    e.dataTransfer.effectAllowed = "move";
  }

  function onDragOver(e: React.DragEvent) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }

  async function onDrop(e: React.DragEvent, columnKey: string) {
    e.preventDefault();
    const leadId = e.dataTransfer.getData("text/lead_id");
    if (!leadId || !runId) return;

    let stage = columnKey;
    let tier: string | undefined;
    let included = true;
    let assign: boolean | undefined;

    if (columnKey === "assigned") {
      stage = "assigned";
      tier = "A";
      assign = true;
    } else if (columnKey === "assigned_B") {
      stage = "assigned";
      tier = "B";
      assign = true;
    } else if (columnKey === "excluded") {
      stage = "excluded";
      included = false;
      assign = false;
    } else {
      assign = false;
    }

    setSaving(true);
    setMsg("");
    try {
      const res: any = await admin.patchRunLead(runId, leadId, { stage, tier, included, assign });
      const c = res?.run_counts;
      setMsg(
        c
          ? `Updated → ${columnKey.replace(/_/g, " ")} · funnel ${c.discovered ?? "?"}→${c.enriched ?? "?"}→${c.gated_ok ?? c.gated ?? "?"}→${c.assigned ?? "?"} (delivered ${c.delivered ?? "?"})`
          : `Updated → ${columnKey.replace(/_/g, " ")}`
      );
      load();
    } catch (err: any) {
      setMsg(err.message || "Update failed");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <div className="page"><PageHeader eyebrow="Admin operations" title="Run board" /><div className="card-pad animate-pulse">Loading board…</div></div>;

  return (
    <div className="page">
      <header className="mb-4 shrink-0">
        <Link href="/admin/runs" className="text-xs text-ink-faint hover:text-brand-600 dark:text-brand-500">
          ← Run history
        </Link>
        <PageHeader eyebrow="Admin operations" title="Run board" description="Quality-gated candidates are assigned automatically until the effective target is reached. Manual drag/drop is for operator corrections only." />
        {run && operation ? <OperationStateCard operation={operation} title="Run operation state" onRefresh={refresh} /> : null}
        {run && !runTerminal ? (
          <div className="mt-3 flex justify-end">
            <button type="button" className="btn-secondary border-rust-300 text-rust-700 dark:border-rust-500/50 dark:text-rust-300" onClick={() => void stopRun()} disabled={stopping || saving}>
              {stopping ? "Stopping…" : "Stop run"}
            </button>
          </div>
        ) : null}
        {run && (
          <div className="run-summary-grid mt-4">
            <div><span className="run-summary-label">Status</span><b className="run-summary-value">{run.status}</b></div>
            <div><span className="run-summary-label">Funnel</span><b className="run-summary-value">{run.discovered ?? 0} → {run.enriched ?? 0} → {run.gated_ok ?? 0}</b></div>
            <div><span className="run-summary-label">Delivery target</span><b className={`run-summary-value ${targetReached ? "text-signal-600 dark:text-signal-300" : ""}`}>{delivered} / {target || "—"}{targetReached ? " · reached" : ""}</b><span className="mt-1 block text-[10px] text-ink-faint">Assigned rows: {assigned}</span></div>
            <div><span className="run-summary-label">Delivery</span><b className="run-summary-value">{run.delivered ?? 0}</b></div>
          </div>
        )}
        {run && (
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-faint" aria-label="Run operational evidence">
            <span>Queue age: {run.queue_age_seconds ?? "—"}s</span>
            <span>Retries: {run.retries ?? run.retry_count ?? 0}</span>
            <span>Worker: {run.worker_status || "—"}</span>
            <span>Job: {run.job_id || "—"}</span>
            {run.terminal_reason ? <span>Terminal: {run.terminal_reason}</span> : null}
          </div>
        )}
        {run && !targetReached && target > 0 && (
          <div className="mt-3 rounded-xl border border-amber-300/60 bg-amber-50/70 px-3 py-2 text-xs text-amber-900 dark:bg-amber-500/10 dark:text-amber-200">
            {target - delivered} delivered lead{target - delivered === 1 ? "" : "s"} short. {run.meta?.quota_remaining_after === 0 ? "Tenant quota is exhausted." : "See blockers below for CRM persistence, delivery, quality, exclusivity, or recency failures."}
          </div>
        )}
        {blockers.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-ink-faint">
            <span className="font-semibold text-ink-muted">Assignment blockers:</span>
            {blockers.map(([reason, count]) => <span key={reason} className="pill bg-surface-3 text-ink-muted">{reason.replaceAll("_", " ")} · {count}</span>)}
          </div>
        )}
        {error && <ErrorState error={new Error(error)} onRetry={refresh} />}
        {pollingExpired && <PollingDeadlineState onRefresh={refresh} />}
        {msg && <p className="mt-1 text-xs text-signal-400">{msg}</p>}
        {saving && <p className="text-xs text-ink-faint">Saving…</p>}
      </header>

      <div className="grid min-h-[420px] grid-cols-1 gap-3 overflow-x-auto pb-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
        {COLUMNS.map((col) => (
          <div
            key={col.key}
            onDragOver={onDragOver}
            onDrop={(e) => onDrop(e, col.key)}
            className={`run-column run-column-${col.key} flex min-h-[360px] flex-col rounded-xl border bg-surface-2 shadow-sm`}
          >
            <div className="border-b border-line px-3 py-2">
              <div className="text-sm font-medium text-ink">{col.title}</div>
              <div className="text-[11px] text-ink-faint">
                {col.hint} · {buckets[col.key]?.length || 0}
              </div>
            </div>
            <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-2">
              {(buckets[col.key] || []).map((L) => (
                <LeadCard key={L.lead_id} lead={L} onDragStart={onDragStart} />
              ))}
              {(buckets[col.key] || []).length === 0 && (
                <div className="rounded-lg border border-dashed border-line px-2 py-8 text-center text-[11px] text-ink-muted">
                  <span className="text-ink-faint">No cards yet — open Board after a run that wrote cards, or refresh</span>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
