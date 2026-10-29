"use client";

import { useCallback, useEffect, useState } from "react";
import { crmApprovals } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { Notice, PageHeader, Panel, StatusBadge } from "@/components/ui/Primitives";
import { Icon } from "@/components/ui/Icon";
import { DataState, EmptyState, LoadingRows } from "@/components/ui/States";

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

  return <div className="page">
    <PageHeader
      eyebrow="Agent governance"
      title="Approval inbox"
      description="Outbound drafts stay blocked until a workspace member approves them."
      actions={<button type="button" aria-label="Refresh approvals" className="btn-secondary" onClick={() => void refresh()} disabled={loading}><Icon name="refresh" size={14} />Refresh</button>}
      meta={<span className="tabular-nums">{loading && !items.length ? "Checking inbox…" : `${items.length} waiting for review`}</span>}
    />
    {message ? <Notice tone={message.tone}>{message.text}</Notice> : null}

    <section className="panel flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between" aria-labelledby="approvals-auto-title">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id="approvals-auto-title" className="section-title">Outreach auto-approve</h2>
          <StatusBadge status={auto ? "active" : "paused"} label={auto ? "On" : "Off"} />
        </div>
        <p className="section-sub">When enabled, outreach can move directly into the configured execution path.</p>
      </div>
      <button type="button" role="switch" aria-checked={auto} aria-labelledby="approvals-auto-title" disabled={busy} onClick={() => void toggle()} className={`${auto ? "btn-primary" : "btn-secondary"} shrink-0`}>{auto ? "Enabled" : "Disabled"}</button>
    </section>

    <Panel
      id="approvals-queue"
      title={<span className="tabular-nums">{items.length} outbound draft{items.length === 1 ? "" : "s"}</span>}
      description="Needs approval"
      bodyClassName="p-0"
    >
      <DataState
        loading={loading}
        error={error}
        isEmpty={!items.length}
        empty={<EmptyState title="Nothing is waiting for review" hint="New outbound drafts will appear here when an agent requests approval." icon={<Icon name="check" size={18} />} />}
        onRetry={() => void refresh()}
        skeleton={<LoadingRows rows={3} cols={3} />}
      >
        <ul className="divide-y divide-line" aria-label="Drafts awaiting approval">{items.map((item) => <li key={item.id} className="px-5 py-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className="text-[0.875rem] font-semibold text-ink">Outreach draft</h3>
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink-faint">
                <span>Lead <code className="font-mono text-xs text-ink-muted">{item.lead_id || "—"}</code></span>
                <span className="tabular-nums">{item.created_at ? new Date(item.created_at).toLocaleString() : "Date unavailable"}</span>
              </div>
            </div>
            <StatusBadge status="needs_review" label="Needs approval" />
          </div>
          <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-surface-3/60 p-4 font-mono text-xs leading-5 text-ink-muted">{JSON.stringify(item.payload || {}, null, 2)}</pre>
          <div className="mt-3 flex justify-end gap-2">
            <button type="button" disabled={busy} onClick={() => void decide(item.id, false)} className="btn-secondary btn-sm">Reject</button>
            <button type="button" disabled={busy} onClick={() => void decide(item.id, true)} className="btn-primary btn-sm"><Icon name="check" size={14} />Approve</button>
          </div>
        </li>)}</ul>
      </DataState>
    </Panel>
  </div>;
}
