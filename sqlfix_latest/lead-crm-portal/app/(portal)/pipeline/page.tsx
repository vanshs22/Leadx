"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { crm, PipelineBoard, LeadCard } from "@/lib/api";
import { getTenantId, getTenantUserId } from "@/lib/tenant";
import { TierBadge } from "@/components/leads/TierBadge";
import { useCrmChange } from "@/lib/crmRealtime";
import { ErrorState } from "@/components/ui/States";

function LeadMiniCard({
  lead,
  onDragStart,
}: {
  lead: LeadCard;
  onDragStart: (assignmentId: string) => void;
}) {
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
      className="cursor-grab active:cursor-grabbing rounded-lg border border-line bg-surface p-3 shadow-sm hover:border-brand-500/50 transition"
    >
      <Link
        href={`/leads/${lead.assignment_id}`}
        onClick={(e) => e.stopPropagation()}
        className="block"
      >
        <div className="flex items-start justify-between gap-2">
          <span className="text-sm font-medium text-ink line-clamp-2">
            {lead.company_name || "Lead"}
          </span>
          <TierBadge tier={lead.tier} />
        </div>
        <div className="mt-2 text-xs text-ink-muted">
          {lead.phone_e164 || lead.phone_normalized || "No phone"}
        </div>
        {lead.email ? (
          <div className="mt-0.5 truncate text-xs text-ink-faint">{lead.email}</div>
        ) : null}
        <div className="mt-2 flex items-center justify-between text-xs">
          <span className="tabular-nums text-ink-muted">Score {lead.total_score ?? 0}</span>
          {lead.city ? <span className="text-ink-faint">{lead.city}</span> : null}
        </div>
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

  if (loading && !board) {
    return (
      <div className="page">
        <div className="flex items-end justify-between gap-4">
          <div><div className="skeleton h-8 w-36" /><div className="skeleton mt-2 h-4 w-64" /></div>
        </div>
        <div className="grid min-w-[980px] grid-cols-7 gap-3 overflow-hidden">
          {Array.from({ length: 7 }).map((_, i) => (
            <div key={i} className="card-pad min-h-[420px] space-y-3">
              <div className="skeleton h-5 w-24" />
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
        <ErrorState error={error} onRetry={load} />
        <button aria-label="Seed default stages" className="btn-secondary" onClick={async () => { await crm.setup(getTenantId()); await load(true); }}>
          Seed default stages
        </button>
      </div>
    );
  }
  if (!board) {
    return <div className="page"><div className="card-pad text-ink-faint">No pipeline data yet.</div></div>;
  }

  return (
    <div className="page flex min-h-0 flex-col">
      <header className="mb-4 flex shrink-0 flex-wrap items-end justify-between gap-4">
        <div>
          <div className="eyebrow">Revenue workspace</div>
          <h1 className="page-title mt-1">Pipeline</h1>
          <p className="page-sub">Drag a lead to move it. The change is persisted to CRM + database immediately.</p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`pill ${saving ? "bg-signal-50 text-signal-700 dark:bg-signal-500/10 dark:text-signal-300" : ""}`}>{saving ? "Saving" : msg || "Live"}</span>
          <button aria-label="Action" className="btn-secondary" onClick={() => load()} disabled={loading}>{loading ? "Refreshing…" : "Refresh"}</button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 gap-3 overflow-x-auto overflow-y-hidden pb-1">
        {board.stages.map((stage) => (
          <div
            key={stage.id}
            className={`flex h-full w-80 shrink-0 flex-col rounded-2xl border bg-surface-2/70 shadow-sm transition-all ${dragOverStage === stage.id ? "border-brand-500 bg-brand-50/50 shadow-lift dark:bg-brand-500/10" : "border-line"}`}
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
            <div
              className="flex shrink-0 items-center justify-between border-b border-line px-4 py-3"
              style={{ borderBottomColor: stage.color || undefined }}
            >
              <div className="min-w-0">
                <span className="text-sm font-semibold text-ink">{stage.name}</span>
                <div className="mt-0.5 text-[10px] uppercase tracking-wider text-ink-faint">Stage</div>
              </div>
              <span className="rounded-full border border-line bg-surface px-2.5 py-1 text-xs font-semibold tabular-nums text-ink-muted">
                {stage.leads?.length ?? 0}
              </span>
            </div>
            <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-2">
              {(stage.leads || []).map((lead) => (
                <LeadMiniCard
                  key={lead.assignment_id}
                  lead={lead}
                  onDragStart={setDragId}
                />
              ))}
              {(stage.leads || []).length === 0 && (
                <div className={`m-1 rounded-xl border border-dashed px-3 py-8 text-center text-xs transition ${dragOverStage === stage.id ? "border-brand-400 bg-brand-50/60 text-brand-700 dark:bg-brand-500/10 dark:text-brand-300" : "border-line text-ink-faint"}`}>
                {dragOverStage === stage.id ? "Release to move here" : "Drop leads here"}
              </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {board.unstaged && board.unstaged.length > 0 && (
        <div
          className="mt-4 shrink-0 rounded-xl border border-dashed border-line p-4"
          onDragOver={(e) => e.preventDefault()}
        >
          <h3 className="text-sm font-medium text-ink-muted">
            Unstaged ({board.unstaged.length}) — open lead and set stage, or drag into a column
          </h3>
          <div className="mt-2 flex max-h-40 flex-wrap gap-2 overflow-y-auto">
            {board.unstaged.map((l) => (
              <div
                key={l.assignment_id}
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData("text/assignment_id", l.assignment_id);
                  setDragId(l.assignment_id);
                }}
                className="cursor-grab rounded-lg bg-surface px-3 py-1.5 text-xs text-ink-muted hover:bg-surface-3"
              >
                <Link href={`/leads/${l.assignment_id}`}>{l.company_name || l.assignment_id}</Link>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
