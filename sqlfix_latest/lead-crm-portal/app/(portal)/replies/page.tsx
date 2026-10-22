"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { crm } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/States";
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

  useEffect(() => {
    setLoading(true);
    setError(null);
    crm.replies(getTenantId(), filter || undefined)
      .then((d: any) => setReplies(d.replies || []))
      .catch((e) => setError(e))
      .finally(() => setLoading(false));
  }, [filter, refreshKey]);

  useCrmChange(() => {
    setLoading(true);
    crm.replies(getTenantId(), filter || undefined).then((d: any) => setReplies(d.replies || [])).catch(() => {}).finally(() => setLoading(false));
  }, { tables: ["email_replies", "email_sequence_sends", "lead_outcome_events"] });

  const counts = replies.reduce((acc: Record<string, number>, r) => { acc[r.category] = (acc[r.category] || 0) + 1; return acc; }, {});

  return (
    <div className="page">
      <header>
        <h1 className="page-title">Replies</h1>
        <p className="page-sub">Inbound email responses, categorized by the API so follow-up intent stays visible.</p>
        <div className="mt-2 flex flex-wrap items-center gap-3"><FreshnessNote label="Live reply stream" detail={`${replies.length} in this view`} /><span className="text-[11px] text-ink-faint">Unclassified means the API has not assigned a category yet.</span></div>
      </header>

      <div className="flex flex-wrap gap-2">
        {["", "positive", "negative", "question", "opt_out", "unclassified"].map((c) => (
          <button aria-label="Action"
            key={c || "all"}
            onClick={() => setFilter(c)}
            className={filter === c ? "pill bg-brand-600 text-white" : "pill border border-line bg-surface-2 text-ink-muted"}
          >
            {c || "All"} {c && counts[c] ? `(${counts[c]})` : ""}
          </button>
        ))}
      </div>

      {loading ? <div className="card"><LoadingRows rows={4} cols={3} /></div> : error ? <ErrorState error={error} onRetry={() => setRefreshKey((value) => value + 1)} /> : replies.length === 0 ? <div className="card"><EmptyState title="No replies in this view" hint={filter ? "Try All or wait for a new inbound response." : "Replies will appear here when the connected mailbox reports them."} /></div> : (
      <div className="space-y-2">
        {replies.map((r) => (
          <div key={r.id} className="card-pad">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className={`pill ${CATEGORY_STYLE[r.category] || CATEGORY_STYLE.unclassified}`}>{r.category}</span>
                  <span className="truncate text-sm font-medium text-ink">{r.from_email}</span>
                </div>
                <p className="mt-1 text-sm text-ink-muted">{r.subject}</p>
                <p className="mt-2 whitespace-pre-wrap text-sm text-ink-faint">{(r.body_text || "").slice(0, 400)}</p>
              </div>
              <div className="shrink-0 text-right">
                <div className="text-xs text-ink-faint">{new Date(r.received_at).toLocaleString()}</div>
                {r.assignment_id && (
                  <Link href={`/leads/${r.assignment_id}`} className="mt-1 inline-block text-xs font-medium text-brand-600 hover:underline dark:text-brand-300">
                    Open lead →
                  </Link>
                )}
              </div>
            </div>
          </div>
        ))}
        {replies.length === 0 && <p className="text-sm text-ink-faint">No replies yet</p>}
      </div>
      )}
    </div>
  );
}
