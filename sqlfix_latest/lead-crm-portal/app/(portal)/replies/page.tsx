"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { crm } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { useLiveLoader } from "@/lib/live";
import { EmptyState, ErrorState, LoadingRows, PollingDeadlineState } from "@/components/ui/States";
import { PageHeader, FilterBar, Badge, StatusBadge, type Tone } from "@/components/ui/Primitives";
import { FreshnessNote } from "@/components/ui/SurfaceMeta";
import { Icon } from "@/components/ui/Icon";

/** Intent chip tone per API category: interest = emerald, objection = rust, question = navy. */
const CATEGORY_TONE: Record<string, Tone> = {
  positive: "success",
  negative: "danger",
  opt_out: "warn",
  question: "brand",
  unclassified: "neutral",
};

const CATEGORY_LABEL: Record<string, string> = {
  positive: "Positive",
  negative: "Negative",
  opt_out: "Opt-out",
  question: "Question",
  unclassified: "Unclassified",
};

function formatReceived(value?: string | null): { short: string; full: string } {
  if (!value) return { short: "Time unavailable", full: "Time unavailable" };
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return { short: "Time unavailable", full: "Time unavailable" };
  const sameDay = d.toDateString() === new Date().toDateString();
  return {
    short: sameDay ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : d.toLocaleDateString([], { month: "short", day: "numeric" }),
    full: d.toLocaleString(),
  };
}

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

  return <div className="page">
    <PageHeader eyebrow="Engage" title="Replies" description="Inbound email responses, categorized by the API so follow-up intent and stop reasons stay visible." actions={<button type="button" aria-label="Refresh replies" className="btn-secondary" onClick={refresh}><Icon name="refresh" size={14} />Refresh</button>} meta={<FreshnessNote label="Live reply stream" detail={`${replies.length} in this view · read-side refresh only`} />} />
    {deadline ? <PollingDeadlineState onRefresh={refresh} /> : null}
    <FilterBar onClear={() => setFilter("")} clearLabel="Show all categories">
      <div className="tabs flex-wrap">
        {["", "positive", "negative", "question", "opt_out", "unclassified"].map((category) => <button type="button" aria-pressed={filter === category} aria-label={`Filter replies by ${category || "all categories"}`} key={category || "all"} onClick={() => setFilter(category)} className={filter === category ? "tab-active" : "tab"}>{category ? CATEGORY_LABEL[category] : "All"}{category && counts[category] ? <span className="ml-1.5 tabular-nums text-ink-faint">{counts[category]}</span> : null}</button>)}
      </div>
    </FilterBar>
    {loading && !replies.length ? <div className="card"><LoadingRows rows={4} cols={3} /></div> : error && !replies.length ? <ErrorState error={error} onRetry={refresh} /> : replies.length === 0 ? <div className="card"><EmptyState title="No replies in this view" hint={filter ? "Try All or wait for a new inbound response." : "Replies appear when the connected mailbox reports them."} icon={<Icon name="reply" size={18} />} /></div> : <section className="panel" aria-label="Reply inbox">
      {error ? <div className="border-b border-line p-4"><ErrorState error={error} onRetry={refresh} /></div> : null}
      <div className="panel-header">
        <h2 className="panel-title">Inbox</h2>
        <span className="text-xs tabular-nums text-ink-faint">{replies.length} {replies.length === 1 ? "reply" : "replies"}</span>
      </div>
      <ul className="divide-y divide-line">
        {replies.map((reply) => {
          const category = reply.category || "unclassified";
          const when = formatReceived(reply.received_at);
          const sender = reply.from_email || "Sender unavailable";
          return <li key={reply.id}>
            <article className="flex gap-3 px-4 py-3.5 transition-colors hover:bg-surface-3/50 sm:px-5">
              <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-line bg-surface-3 text-xs font-semibold uppercase text-ink-muted" aria-hidden="true">{reply.from_email ? reply.from_email.slice(0, 1) : "?"}</span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="truncate text-[0.8125rem] font-semibold text-ink">{sender}</span>
                  <time dateTime={reply.received_at || undefined} title={when.full} className="shrink-0 text-xs tabular-nums text-ink-faint">{when.short}</time>
                </div>
                <div className="mt-0.5 flex flex-wrap items-center gap-2">
                  <Badge tone={CATEGORY_TONE[category] || "neutral"}>{CATEGORY_LABEL[category] || category}</Badge>
                  <p className="min-w-0 flex-1 truncate text-[0.8125rem] text-ink">{reply.subject || "No subject"}</p>
                </div>
                <p className="mt-1 line-clamp-2 whitespace-pre-wrap text-[0.8125rem] leading-5 text-ink-muted">{(reply.body_text || "Body unavailable").slice(0, 400)}</p>
                <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[11px] text-ink-faint">
                  {reply.status ? <StatusBadge status={reply.status} /> : <span>Status: unavailable</span>}
                  <span>Sequence: {reply.sequence_id ? "linked" : "not linked"}</span>
                  <span>Automation: {reply.automation_run_id ? "linked" : "not linked"}</span>
                  {reply.assignment_id ? <Link href={`/leads/${reply.assignment_id}`} className="link ml-auto inline-flex items-center gap-1 text-xs">Open lead <Icon name="arrow" size={12} /></Link> : null}
                </div>
              </div>
            </article>
          </li>;
        })}
      </ul>
    </section>}
  </div>;
}
