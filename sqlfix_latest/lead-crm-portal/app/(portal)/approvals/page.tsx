"use client";

import { useCallback, useEffect, useState } from "react";
import { crmApprovals } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { PageHeader, Notice } from "@/components/ui/Primitives";
import { DataState, EmptyState } from "@/components/ui/States";

export default function ApprovalsPage() {
  const tid = getTenantId();
  const [items, setItems] = useState<any[]>([]);
  const [auto, setAuto] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [message, setMessage] = useState<{ tone: "success" | "danger" | "neutral"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    if (!tid) { setLoading(false); setError(new Error("Choose a workspace first.")); return; }
    setLoading(true); setError(null);
    try {
      const r = await crmApprovals.list(tid);
      setItems(r.items || []); setAuto(Boolean(r.outreach_auto_approve));
    } catch (caught) { setError(caught); }
    finally { setLoading(false); }
  }, [tid]);

  useEffect(() => { void refresh(); }, [refresh]);
  useCrmChange(() => { void refresh(); }, { tables: ["approval_inbox", "agent_approvals", "email_replies", "lead_assignments"] });

  async function decide(id: string, approved: boolean) {
    if (!tid) return;
    setBusy(true); setMessage(null);
    try { await crmApprovals.decide(tid, id, approved); setMessage({ tone: "success", text: approved ? "Draft approved." : "Draft rejected." }); await refresh(); }
    catch (caught) { setMessage({ tone: "danger", text: caught instanceof Error ? caught.message : "Could not update approval." }); }
    finally { setBusy(false); }
  }
  async function toggle() {
    if (!tid) return;
    setBusy(true); setMessage(null);
    try { await crmApprovals.settings(tid, !auto); setAuto(!auto); setMessage({ tone: "success", text: `Outreach auto-approve ${!auto ? "enabled" : "disabled"}.` }); }
    catch (caught) { setMessage({ tone: "danger", text: caught instanceof Error ? caught.message : "Could not update setting." }); }
    finally { setBusy(false); }
  }

  return <div className="page space-y-5">
    <PageHeader eyebrow="Agent governance" title="Approval inbox" description="Outbound drafts stay blocked until a workspace member approves them." actions={<button aria-label="Refresh approvals" className="btn-secondary" onClick={() => void refresh()} disabled={loading}>Refresh</button>} />
    {message ? <Notice tone={message.tone}>{message.text}</Notice> : null}
    <section className="premium-card flex flex-wrap items-center justify-between gap-4 p-5"><div><div className="font-semibold text-ink">Outreach auto-approve</div><p className="mt-1 text-xs text-ink-muted">When enabled, outreach can move directly into the configured execution path.</p></div><button disabled={busy} onClick={() => void toggle()} className={auto ? "btn-primary" : "btn-secondary"}>{auto ? "Enabled" : "Disabled"}</button></section>
    <section className="premium-card overflow-hidden"><div className="border-b border-line px-5 py-4"><div className="label">Needs approval</div><h2 className="mt-1 text-lg font-semibold text-ink">{items.length} outbound draft{items.length === 1 ? "" : "s"}</h2></div><DataState loading={loading} error={error} isEmpty={!items.length} empty={<EmptyState title="Nothing is waiting for review" hint="New outbound drafts will appear here when an agent requests approval." />} onRetry={() => void refresh()} skeleton={<div className="p-5 text-sm text-ink-muted">Loading approval inbox…</div>}><div className="divide-y divide-line">{items.map((item) => <article key={item.id} className="p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="font-semibold text-ink">Outreach draft</div><div className="mt-1 text-xs text-ink-faint">Lead {item.lead_id || "—"} · {item.created_at ? new Date(item.created_at).toLocaleString() : "Date unavailable"}</div></div><span className="pill bg-amber-500/10 text-amber-700 dark:text-amber-300">Needs approval</span></div><pre className="mt-4 max-h-64 overflow-auto whitespace-pre-wrap rounded-xl border border-line bg-surface-2 p-4 text-xs text-ink-muted">{JSON.stringify(item.payload || {}, null, 2)}</pre><div className="mt-4 flex gap-2"><button disabled={busy} onClick={() => void decide(item.id, true)} className="btn-primary">Approve</button><button disabled={busy} onClick={() => void decide(item.id, false)} className="btn-secondary">Reject</button></div></article>)}</div></DataState></section>
  </div>;
}
