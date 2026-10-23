"use client";

/**
 * Operator replies inbox. Two modes:
 *  - All clients: triage view, every tenant's inbound in one list.
 *  - Ours only: replies to our own prospecting (the internal tenant).
 */
import { useCallback, useEffect, useState } from "react";

import { adminExtras } from "@/lib/admin";

const CATEGORY_STYLE: Record<string, string> = {
  positive: "bg-signal-50 text-signal-700 dark:bg-signal-500/15 dark:text-signal-300",
  negative: "bg-rust-50 text-rust-700 dark:bg-rust-900/30 dark:text-rust-300",
  opt_out: "bg-surface-3 text-ink-faint",
  question: "bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-700 dark:text-brand-300",
  unclassified: "bg-surface-3 text-ink-muted",
};

const CATEGORIES = ["", "positive", "question", "negative", "opt_out", "unclassified"];

export default function AdminRepliesPage() {
  const [replies, setReplies] = useState<any[]>([]);
  const [category, setCategory] = useState("");
  const [ownOnly, setOwnOnly] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d: any = await adminExtras.replies({
        category: category || undefined,
        operator_only: ownOnly || undefined,
      });
      setReplies(d.replies || []);
      setNote(d.note || "");
      setError("");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [category, ownOnly]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="page">
      <header>
        <h1 className="page-title">Replies</h1>
        <p className="page-sub">
          {ownOnly
            ? "Responses to our own outbound prospecting"
            : "Inbound across every client — positives first"}
        </p>
      </header>

      {error && <p className="text-sm text-rust-500 dark:text-rust-300">{error}</p>}
      {note === "operator_tenant_not_created" && (
        <p className="text-sm text-ink-muted">
          The internal workspace doesn&apos;t exist yet — set it up on the Prospecting page.
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {CATEGORIES.map((c) => (
          <button aria-label="Action"
            key={c || "all"}
            onClick={() => setCategory(c)}
            className={
              category === c
                ? "pill bg-signal-600 text-white"
                : "pill border border-line bg-surface-2 text-ink-muted"
            }
          >
            {c || "All"}
          </button>
        ))}
        <label className="ml-auto flex items-center gap-2 text-xs text-ink-muted">
          <input
            type="checkbox"
            checked={ownOnly}
            onChange={(e) => setOwnOnly(e.target.checked)}
            className="rounded border-line"
          />
          Ours only
        </label>
      </div>

      {loading && <p className="text-sm text-ink-faint">Loading…</p>}

      <div className="space-y-2">
        {replies.map((r) => (
          <div key={r.id} className="card-pad">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`pill ${CATEGORY_STYLE[r.category] || CATEGORY_STYLE.unclassified}`}>
                    {r.category}
                  </span>
                  <span className="truncate text-sm font-medium text-ink">{r.from_email}</span>
                  {r.tenant_name ? (
                    <span className="text-[11px] text-ink-faint">· {r.tenant_name}</span>
                  ) : null}
                </div>
                <p className="mt-1 text-sm text-ink-muted">{r.subject}</p>
                <p className="mt-2 whitespace-pre-wrap text-sm text-ink-faint">
                  {(r.body_text || "").slice(0, 600)}
                </p>
              </div>
              <div className="shrink-0 text-right text-xs text-ink-faint">
                {r.received_at ? new Date(r.received_at).toLocaleString() : ""}
              </div>
            </div>
          </div>
        ))}
        {!loading && !replies.length && (
          <p className="text-sm text-ink-faint">No replies match this filter.</p>
        )}
      </div>
    </div>
  );
}
