"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { crm } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { useLiveLoader } from "@/lib/live";
import { EmptyState, ErrorState, LoadingRows, PollingDeadlineState } from "@/components/ui/States";
import { PageHeader, FilterBar } from "@/components/ui/Primitives";
import { FreshnessNote } from "@/components/ui/SurfaceMeta";

const CATEGORY_STYLE: Record<string, string> = {
  positive: "bg-signal-50 text-signal-700 dark:bg-signal-500/15 dark:text-signal-300",
  negative: "bg-rust-50 text-rust-700 dark:bg-rust-900/30 dark:text-rust-300",
  opt_out: "bg-surface-3 text-ink-faint",
  question: "bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-300",
  unclassified: "bg-surface-3 text-ink-muted",
};

export default function RepliesPage() {
  const [replies, setReplies] = useState<any[]>([]);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [refreshKey, setRefreshKey] = useState(0);
  const [deadline, setDeadline] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try { const d: any = await crm.replies(getTenantId(), filter || undefined); setReplies(d.replies || []); }
    catch (caught) { setError(caught); }
    finally { setLoading(false); }
  }, [filter]);
  useLiveLoader(load, 30_000, [refreshKey], { onDeadline: () => setDeadline(true) });
  useCrmChange(() => { void load(); }, { tables: ["email_replies", "email_sequence_sends", "lead_outcome_events"] });

  const counts = replies.reduce((acc: Record<string, number>, r) => { acc[r.category] = (acc[r.category] || 0) + 1; return acc; }, {});
  const refresh = () => { setDeadline(false); setRefreshKey((value) => value + 1); };

  return <div className="page space-y-5">
    <PageHeader eyebrow="Engage" title="Replies" description="Inbound email responses, categorized by the API so follow-up intent and stop reasons stay visible." actions={<button type="button" aria-label="Refresh replies" className="btn-secondary" onClick={refresh}>Refresh</button>} meta={<FreshnessNote label="Live reply stream" detail={`${replies.length} in this view · read-side refresh only`} />} />
    {deadline ? <PollingDeadlineState onRefresh={refresh} /> : null}
    <FilterBar onClear={() => setFilter("")} clearLabel="Show all categories">{["", "positive", "negative", "question", "opt_out", "unclassified"].map((category) => <button type="button" aria-pressed={filter === category} aria-label={`Filter replies by ${category || "all categories"}`} key={category || "all"} onClick={() => setFilter(category)} className={filter === category ? "pill bg-brand-600 text-white" : "pill border border-line bg-surface-2 text-ink-muted"}>{category || "All"} {category && counts[category] ? `(${counts[category]})` : ""}</button>)}</FilterBar>
    {loading && !replies.length ? <div className="card"><LoadingRows rows={4} cols={3} /></div> : error && !replies.length ? <ErrorState error={error} onRetry={refresh} /> : replies.length === 0 ? <div className="card"><EmptyState title="No replies in this view" hint={filter ? "Try All or wait for a new inbound response." : "Replies appear when the connected mailbox reports them."} /></div> : <div className="space-y-2">{error ? <ErrorState error={error} onRetry={refresh} /> : null}{replies.map((reply) => <article key={reply.id} className="card-pad"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="flex items-center gap-2"><span className={`pill ${CATEGORY_STYLE[reply.category] || CATEGORY_STYLE.unclassified}`}>{reply.category || "unclassified"}</span><span className="truncate text-sm font-medium text-ink">{reply.from_email || "Sender unavailable"}</span></div><p className="mt-1 text-sm text-ink-muted">{reply.subject || "No subject"}</p><p className="mt-2 whitespace-pre-wrap text-sm text-ink-faint">{(reply.body_text || "Body unavailable").slice(0, 400)}</p></div><div className="shrink-0 text-right"><div className="text-xs text-ink-faint">{reply.received_at ? new Date(reply.received_at).toLocaleString() : "Time unavailable"}</div>{reply.assignment_id ? <Link href={`/leads/${reply.assignment_id}`} className="mt-1 inline-block text-xs font-medium text-brand-600 hover:underline dark:text-brand-300">Open lead →</Link> : null}</div></div><div className="mt-3 flex flex-wrap gap-2 text-[11px] text-ink-faint"><span>Status: {reply.status || "unavailable"}</span><span>Sequence: {reply.sequence_id ? "linked" : "not linked"}</span><span>Automation: {reply.automation_run_id ? "linked" : "not linked"}</span></div></article>)}</div>}
  </div>;
}
