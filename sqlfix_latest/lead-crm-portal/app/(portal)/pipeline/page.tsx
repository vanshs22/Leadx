"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { crm, PipelineBoard, LeadCard } from "@/lib/api";
import { getTenantId, getTenantUserId } from "@/lib/tenant";
import { TierBadge } from "@/components/leads/TierBadge";
import { useCrmChange } from "@/lib/crmRealtime";
import { ErrorState } from "@/components/ui/States";
import { EmptyState } from "@/components/ui/States";
import { Badge, PageHeader, StatusBadge } from "@/components/ui/Primitives";
import { Icon } from "@/components/ui/Icon";
import { formatDate, formatMoney } from "@/lib/crm";

/** 2px top rule per column: won = emerald, lost = rust, everything else = navy ink. */
function stageToneClass(stage: { is_won?: boolean | null; is_lost?: boolean | null }): string {
  if (stage.is_won) return "border-t-emerald-600 dark:border-t-emerald-400";
  if (stage.is_lost) return "border-t-rust-500 dark:border-t-rust-400";
  return "border-t-brand-600 dark:border-t-brand-300";
}

function stageValue(leads: LeadCard[]): number | null {
  const withValue = leads.filter((l) => typeof l.value_estimate === "number");
  if (withValue.length === 0) return null;
  return withValue.reduce((acc, l) => acc + (l.value_estimate || 0), 0);
}

function LeadMiniCard({
  lead,
  onDragStart,
}: {
  lead: LeadCard;
  onDragStart: (assignmentId: string) => void;
}) {
  const owner = lead.owner_name || lead.assigned_to || "";
  return (
    <div
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData("text/assignment_id", lead.assignment_id);
        e.dataTransfer.setData("text/plain", lead.assignment_id);
        e.dataTransfer.effectAllowed = "move";
        onDragStart(lead.assignment_id);
      }}
      onDragEnd={() => onDragStart("")}
      className="lead-card cursor-grab rounded-lg border border-line bg-surface-2 p-3 shadow-card transition-colors hover:border-line-strong active:cursor-grabbing"
    >
      <Link
        href={`/leads/${lead.assignment_id}`}
        onClick={(e) => e.stopPropagation()}
        className="block focus-visible:rounded-md"
      >
        <div className="flex items-start justify-between gap-2">
          <span className="line-clamp-2 text-[0.8125rem] font-semibold leading-snug text-ink">
            {lead.company_name || "Lead"}
          </span>
          <TierBadge tier={lead.tier} />
        </div>
        <div className="mt-1 truncate text-xs tabular-nums text-ink-muted">
          {lead.phone_e164 || lead.phone_normalized || "No phone"}
        </div>
        {lead.email ? (
          <div className="mt-0.5 truncate text-xs text-ink-faint">{lead.email}</div>
        ) : null}
        <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2 text-xs">
          <span className="font-medium tabular-nums text-ink">Score {lead.total_score ?? 0}</span>
          {typeof lead.value_estimate === "number" ? (
            <span className="font-semibold tabular-nums text-ink">{formatMoney(lead.value_estimate)}</span>
          ) : lead.city ? (
            <span className="truncate text-ink-faint">{lead.city}</span>
          ) : null}
        </div>
        {(owner || lead.next_action_at || lead.assignment_status) ? (
          <div className="mt-2 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-2 text-[11px] text-ink-faint">
            <span className="inline-flex min-w-0 items-center gap-1">
              {owner ? <><Icon name="team" size={12} /><span className="truncate">{owner}</span></> : null}
              {lead.next_action_at ? <span className="inline-flex items-center gap-1 tabular-nums"><Icon name="clock" size={12} />{formatDate(lead.next_action_at)}</span> : null}
            </span>
            {lead.assignment_status ? <StatusBadge status={lead.assignment_status} /> : null}
          </div>
        ) : null}
      </Link>
    </div>
  );
}

export default function PipelinePage() {
  const [board, setBoard] = useState<PipelineBoard | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const [dragOverStage, setDragOverStage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  const load = useCallback((silent = false) => {
    const tid = getTenantId();
    if (!tid) {
      setError("No workspace selected");
      return;
    }
    if (!silent) setLoading(true);
    crm
      .pipeline(tid)
      .then((next) => { setBoard(next); setError(null); })
      .catch((e) => setError(e.message))
      .finally(() => { if (!silent) setLoading(false); });
  }, []);

  useEffect(() => { load(); }, [load]);


  // Realtime events are invalidation signals. The API is authoritative, so a
  // successful external CRM change refreshes the board without requiring a
  // manual reload.
  // Only refresh the client pipeline for assignment/stage changes. Discovery,
  // enrichment and run-progress writes are intentionally ignored here; those
  // can happen dozens of times during a target run and should never make the
  // whole board appear to reload.
  useCrmChange(() => { void load(true); }, { tables: ["lead_assignments", "pipeline_stages"] });

  async function moveToStage(assignmentId: string, stageId: string) {
    const tid = getTenantId();
    if (!tid || !assignmentId || !stageId || !board) return;

    // Optimistic: move the card in local state immediately so the drop
    // feels instant, then sync in the background. Previously this did a
    // full board refetch after every single move — correct, but visibly
    // slow/janky on every drag. We keep the optimistic move, then confirm
    // the persisted server state once the write succeeds.
    const prevBoard = board;
    let movedLead: LeadCard | null = null;
    const nextStages = board.stages.map((s) => {
      const idx = s.leads.findIndex((l) => l.assignment_id === assignmentId);
      if (idx === -1) return s;
      movedLead = s.leads[idx];
      return { ...s, leads: s.leads.filter((l) => l.assignment_id !== assignmentId) };
    });
    let nextUnstaged = board.unstaged;
    if (!movedLead) {
      const idx = board.unstaged.findIndex((l) => l.assignment_id === assignmentId);
      if (idx !== -1) {
        movedLead = board.unstaged[idx];
        nextUnstaged = board.unstaged.filter((l) => l.assignment_id !== assignmentId);
      }
    }
    if (!movedLead) return; // card not found locally, nothing to move

    const targetStage = nextStages.find((s) => s.id === stageId);
    const finalStages = targetStage
      ? nextStages.map((s) => (s.id === stageId ? { ...s, leads: [movedLead as LeadCard, ...s.leads] } : s))
      : nextStages;

    if (!targetStage) {
      setMsg("That pipeline stage is no longer available. Refreshing…");
      await load();
      return;
    }
    setBoard({ stages: finalStages, unstaged: nextUnstaged });
    setDragId(null);
    setDragOverStage(null);
    setSaving(true);
    setMsg("Saving…");

    try {
      await crm.changeStage(tid, assignmentId, stageId);
      // The card is already updated optimistically. Confirm in the background
      // without putting the whole board into a loading state.
      void load(true);
      setMsg("Saved everywhere");
    } catch (e: any) {
      setMsg(e?.message || "Move failed — reverting");
      setBoard(prevBoard);
    } finally {
      setSaving(false);
      setDragOverStage(null);
    }
  }

  const header = (actions?: React.ReactNode) => (
    <PageHeader
      eyebrow="Revenue workspace"
      title="Lead lifecycle"
      description="Drag a lead to move it. The change is persisted to CRM + database immediately."
      actions={actions}
    />
  );

  if (loading && !board) {
    return (
      <div className="page">
        {header()}
        <div className="flex gap-3 overflow-hidden" aria-busy="true" aria-live="polite">
          <span className="sr-only">Loading pipeline…</span>
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="w-72 shrink-0 space-y-2 rounded-xl border border-line border-t-2 border-t-line-strong bg-surface-3/40 p-2">
              <div className="skeleton m-1 h-4 w-24" />
              {Array.from({ length: 3 }).map((__, j) => <div key={j} className="skeleton h-24 w-full" />)}
            </div>
          ))}
        </div>
      </div>
    );
  }
  if (error) {
    return (
      <div className="page">
        {header()}
        <ErrorState error={error} onRetry={load} />
        <div>
          <button aria-label="Seed default stages" className="btn-secondary" onClick={async () => { await crm.setup(getTenantId()); await load(true); }}>
            Seed default stages
          </button>
        </div>
      </div>
    );
  }
  if (!board) {
    return <div className="page">{header()}<div className="card"><EmptyState title="No pipeline data yet." hint="Stages and leads appear here once the workspace has assignments." /></div></div>;
  }

  const totalLeads = board.stages.reduce((acc, s) => acc + (s.leads?.length ?? 0), 0) + (board.unstaged?.length ?? 0);

  return (
    <div className="page">
      {header(
        <>
          <span aria-live="polite" className={saving ? "badge-warn" : msg && msg !== "Saved everywhere" ? "badge-neutral" : "badge-success"}>
            {saving ? null : <span className="live-dot !h-1.5 !w-1.5" aria-hidden="true" />}
            {saving ? "Saving" : msg || "Live"}
          </span>
          <button className="btn-secondary" onClick={() => load()} disabled={loading}>
            <Icon name="refresh" size={14} />
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </>
      )}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-muted">
        <span className="tabular-nums"><span className="font-semibold text-ink">{totalLeads}</span> leads</span>
        <span className="tabular-nums"><span className="font-semibold text-ink">{board.stages.length}</span> stages</span>
        {board.unstaged?.length ? <span className="tabular-nums"><span className="font-semibold text-ink">{board.unstaged.length}</span> unstaged</span> : null}
      </div>

      <div className="min-w-0 overflow-x-auto pb-2" role="region" aria-label="Lead lifecycle board">
        <div className="flex w-max gap-3">
          {board.stages.map((stage) => {
            const leads = stage.leads || [];
            const value = stageValue(leads);
            const isOver = dragOverStage === stage.id;
            return (
              <div
                key={stage.id}
                className={`flex w-[17.5rem] shrink-0 flex-col rounded-xl border border-t-2 bg-surface-3/50 transition-colors sm:w-72 ${stageToneClass(stage)} ${isOver ? "border-x-brand-400 border-b-brand-400 bg-brand-50/60 dark:border-x-brand-500/60 dark:border-b-brand-500/60 dark:bg-brand-500/10" : "border-x-line border-b-line"}`}
                onDragEnter={(e) => { e.preventDefault(); setDragOverStage(stage.id); }}
                onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; setDragOverStage(stage.id); }}
                onDragLeave={(e) => { if (e.currentTarget === e.target) setDragOverStage(null); }}
                onDrop={(e) => {
                  e.preventDefault();
                  const id = e.dataTransfer.getData("text/assignment_id") || e.dataTransfer.getData("text/plain") || dragId || "";
                  setDragOverStage(null);
                  if (id) void moveToStage(id, stage.id);
                }}
              >
                <div className="flex shrink-0 items-start justify-between gap-2 border-b border-line px-3 py-2.5">
                  <div className="min-w-0">
                    <h2 className="flex items-center gap-1.5 text-[0.8125rem] font-semibold text-ink">
                      {stage.color ? <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: stage.color }} aria-hidden="true" /> : null}
                      <span className="truncate">{stage.name}</span>
                    </h2>
                    <div className="mt-0.5 text-xs font-medium tabular-nums text-ink-muted">
                      {value === null ? <span className="text-ink-faint">Value unavailable</span> : formatMoney(value)}
                    </div>
                  </div>
                  <span className="badge-neutral tabular-nums">
                    {stage.leads?.length ?? 0}
                  </span>
                </div>
                <div className="flex max-h-[calc(100vh-280px)] min-h-[160px] flex-1 flex-col gap-2 overflow-y-auto p-2">
                  {leads.map((lead) => (
                    <LeadMiniCard
                      key={lead.assignment_id}
                      lead={lead}
                      onDragStart={setDragId}
                    />
                  ))}
                  {leads.length === 0 && (
                    <div className={`rounded-lg border border-dashed px-3 py-8 text-center text-xs transition-colors ${isOver ? "border-brand-400 text-brand-700 dark:text-brand-300" : "border-line-strong text-ink-faint"}`}>
                      {isOver ? "Release to move here" : "Drop leads here"}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {board.unstaged && board.unstaged.length > 0 && (
        <section
          className="panel"
          aria-labelledby="unstaged-title"
          onDragOver={(e) => e.preventDefault()}
        >
          <div className="panel-header">
            <div className="min-w-0">
              <h3 id="unstaged-title" className="section-title">
                Unstaged <span className="tabular-nums text-ink-faint">({board.unstaged.length})</span>
              </h3>
              <p className="section-sub">Open a lead and set its stage, or drag it into a column.</p>
            </div>
            <Badge tone="warn">Needs stage</Badge>
          </div>
          <div className="flex max-h-40 flex-wrap gap-2 overflow-y-auto p-4">
            {board.unstaged.map((l) => (
              <div
                key={l.assignment_id}
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData("text/assignment_id", l.assignment_id);
                  setDragId(l.assignment_id);
                }}
                className="cursor-grab rounded-md border border-line bg-surface-2 px-2.5 py-1.5 text-xs font-medium text-ink-muted shadow-card hover:border-line-strong hover:text-ink"
              >
                <Link href={`/leads/${l.assignment_id}`}>{l.company_name || l.assignment_id}</Link>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
