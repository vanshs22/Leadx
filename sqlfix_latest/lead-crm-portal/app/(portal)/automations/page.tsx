"use client";

import { useCallback, useMemo, useState } from "react";
import { crm } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { useLiveLoader } from "@/lib/live";
import { DataState, EmptyState, ErrorState, LoadingRows, PollingDeadlineState } from "@/components/ui/States";
import { PageHeader, Panel, StatusBadge } from "@/components/ui/Primitives";
import { FreshnessNote } from "@/components/ui/SurfaceMeta";
import { Icon } from "@/components/ui/Icon";

const TRIGGERS = [["email.reply_received", "Reply received"], ["lead.assigned", "Lead assigned"], ["deal.stage_changed", "Deal stage changed"], ["lead.score_changed", "Lead score changed"], ["lead.quality_gate_passed", "Lead passed quality gate"]] as const;
const TEMPLATES = [
  { name: "Hot reply → owner + deal", trigger: "email.reply_received", graph: { trigger: { event: "email.reply_received", conditions: [{ field: "reply_score", op: "gte", value: 75 }] }, actions: [{ type: "escalate_owner", params: { title: "Hot reply — owner action required" } }, { type: "notify_owner", params: { message: "A high-intent reply needs owner attention." } }] } },
  { name: "Opt-out → stop outreach", trigger: "email.reply_received", graph: { trigger: { event: "email.reply_received", conditions: [{ field: "category", op: "eq", value: "opt_out" }] }, actions: [{ type: "stop_sequence" }] } },
  { name: "Positive reply → owner follow-up", trigger: "email.reply_received", graph: { trigger: { event: "email.reply_received", conditions: [{ field: "reply_score", op: "gte", value: 45 }] }, actions: [{ type: "create_task", params: { title: "Follow up on interested reply", priority: "high" } }, { type: "notify_owner", params: { message: "A prospect replied with buying/interested intent." } }] } },
];

const OP_LABEL: Record<string, string> = { gte: "≥", lte: "≤", gt: ">", lt: "<", eq: "=", neq: "≠" };
const triggerLabel = (key: string | null | undefined) => TRIGGERS.find((x) => x[0] === key)?.[1] || key || "unavailable";
const humanize = (value: unknown) => String(value ?? "").replace(/[._]/g, " ");

/** Trigger → conditions → actions, drawn as a vertical step timeline. */
function GraphTimeline({ graph, trigger }: { graph: any; trigger: string | null | undefined }) {
  const conditions: any[] = Array.isArray(graph?.trigger?.conditions) ? graph.trigger.conditions : [];
  const actions: any[] = Array.isArray(graph?.actions) ? graph.actions : [];
  const steps: Array<{ kind: string; text: string; tone: string }> = [
    { kind: "When", text: triggerLabel(graph?.trigger?.event || trigger), tone: "border-brand-300 bg-brand-50 dark:border-brand-500/50 dark:bg-brand-500/20" },
    ...conditions.map((c) => ({ kind: "If", text: `${humanize(c.field)} ${OP_LABEL[c.op] || c.op} ${humanize(c.value)}`, tone: "border-line-strong bg-surface-3" })),
    ...actions.map((a) => ({ kind: "Then", text: humanize(a.type), tone: "border-emerald-300 bg-emerald-50 dark:border-emerald-400/40 dark:bg-emerald-400/15" })),
  ];
  return (
    <ol className="mt-3 space-y-0">
      {steps.map((step, i) => (
        <li key={i} className="relative flex gap-3 pb-2.5 last:pb-0">
          {i < steps.length - 1 ? <span className="absolute left-[5px] top-4 h-[calc(100%-0.5rem)] w-px bg-line-strong" aria-hidden="true" /> : null}
          <span className={`relative mt-1 h-[11px] w-[11px] shrink-0 rounded-full border ${step.tone}`} aria-hidden="true" />
          <div className="min-w-0 text-xs">
            <span className="mr-1.5 font-semibold text-ink">{step.kind}</span>
            <span className="capitalize text-ink-muted">{step.text}</span>
          </div>
        </li>
      ))}
    </ol>
  );
}

export default function AutomationsPage() {
  const [rows, setRows] = useState<any[]>([]); const [name, setName] = useState(""); const [trigger, setTrigger] = useState("email.reply_received");
  const [template, setTemplate] = useState(0); const [enabled, setEnabled] = useState(true); const [busy, setBusy] = useState(false); const [message, setMessage] = useState("");
  const [error, setError] = useState<unknown>(null); const [mutationError, setMutationError] = useState<unknown>(null); const [loading, setLoading] = useState(true); const [deadline, setDeadline] = useState(false); const [refreshKey, setRefreshKey] = useState(0);
  const tid = useMemo(() => getTenantId(), []);
  const load = useCallback(async () => { if (!tid) return; setLoading(true); setError(null); try { const r: any = await crm.automations(tid); setRows(r.automations || []); } catch (caught) { setError(caught); } finally { setLoading(false); } }, [tid]);
  useLiveLoader(load, 30_000, [refreshKey], { onDeadline: () => setDeadline(true) });
  useCrmChange(load, { tables: ["workflow_definitions", "workflow_runs", "automation_events", "email_replies", "lead_assignments"] });
  const refresh = () => { setDeadline(false); setRefreshKey((value) => value + 1); };
  async function create() { const t = TEMPLATES[template]; setBusy(true); setMutationError(null); setMessage(""); try { await crm.createAutomation(tid, { name: name.trim() || t.name, trigger_type: trigger, enabled, graph: { ...t.graph, trigger: { ...(t.graph as any).trigger, event: trigger } } }); setName(""); setMessage("Automation created"); await load(); } catch (e: any) { setMutationError(e); setMessage(""); } finally { setBusy(false); } }
  async function toggle(row: any) { setBusy(true); setMutationError(null); setMessage(""); try { await crm.updateAutomation(tid, row.id, { name: row.name, description: row.description || "", trigger_type: row.trigger_type, graph: row.graph, enabled: !row.enabled }); await load(); } catch (e: any) { setMutationError(e); } finally { setBusy(false); } }
  async function remove(row: any) { if (!window.confirm(`Delete ${row.name || "this automation"}?`)) return; setBusy(true); setMutationError(null); try { await crm.deleteAutomation(tid, row.id); setMessage("Automation deleted"); await load(); } catch (e) { setMutationError(e); } finally { setBusy(false); } }
  async function inspectRuns(row: any) { setMutationError(null); try { const result: any = await crm.automationRuns(tid, row.id); const latest = result.runs?.[0]; setRows((current) => current.map((item) => item.id === row.id ? { ...item, last_run_status: latest?.status, last_error: latest?.error, next_execution: result.next_execution } : item)); } catch (e) { setMutationError(e); } }

  const selectedTemplate = TEMPLATES[template];
  const enabledCount = rows.filter((r) => r.enabled).length;
  const messageTone = /fail|could not/i.test(message) ? "notice-danger" : "notice-success";

  return <div className="page">
    <PageHeader eyebrow="Engage" title="Automations" description="Event-driven actions with explicit trigger, run, reply, error, and retry state. Refresh only reconciles stored records." actions={<button type="button" aria-label="Refresh automations" className="btn-secondary" onClick={refresh}><Icon name="refresh" size={14} />Refresh</button>} meta={<><FreshnessNote label="Live automation telemetry" detail={`${rows.length} definitions · tenant isolated`} /><span className="tabular-nums">{enabledCount} active · {rows.length - enabledCount} paused</span></>} />
    {deadline ? <PollingDeadlineState onRefresh={refresh} /> : null}
    {mutationError ? <ErrorState error={mutationError} onRetry={refresh} /> : null}
    <div className="grid gap-4 lg:grid-cols-3">
      <Panel title="Create automation" description="Pick a trigger and a proven template. You can pause it at any time." className="lg:order-2" footer={<span>Stored as a versioned workflow definition.</span>}>
        <div className="space-y-4">
          <div>
            <label className="field-label" htmlFor="automation-name">Name</label>
            <input id="automation-name" aria-label="Automation name" className="input" placeholder="Automation name" value={name} onChange={(e) => setName(e.target.value)} />
            <p className="field-hint">Leave blank to use the template name.</p>
          </div>
          <div>
            <label className="field-label" htmlFor="automation-trigger">Trigger</label>
            <select id="automation-trigger" aria-label="Automation trigger" className="select" value={trigger} onChange={(e) => setTrigger(e.target.value)}>{TRIGGERS.map((x) => <option key={x[0]} value={x[0]}>{x[1]}</option>)}</select>
          </div>
          <div>
            <label className="field-label" htmlFor="automation-template">Template</label>
            <select id="automation-template" aria-label="Automation template" className="select" value={template} onChange={(e) => setTemplate(Number(e.target.value))}>{TEMPLATES.map((x, i) => <option key={x.name} value={i}>{x.name}</option>)}</select>
          </div>
          <div className="rounded-lg border border-line bg-surface-3/50 p-3">
            <div className="label">Preview</div>
            <GraphTimeline graph={{ ...selectedTemplate.graph, trigger: { ...(selectedTemplate.graph as any).trigger, event: trigger } }} trigger={trigger} />
          </div>
          <label className="flex items-center gap-2 text-[0.8125rem] text-ink"><input aria-label="Enable automation immediately" type="checkbox" className="h-4 w-4 rounded border-line-strong accent-brand-700 dark:accent-brand-300" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Enable immediately</label>
          {message ? <p role="status" aria-live="polite" className={`notice ${messageTone}`}>{message}</p> : null}
          <div className="flex justify-end border-t border-line pt-4">
            <button type="button" aria-label="Create automation" disabled={busy || !tid} onClick={create} className="btn-primary">{busy ? "Creating…" : "Create automation"}</button>
          </div>
        </div>
      </Panel>
      <Panel title="Live automations" description="Stored definitions and latest run fields" className="lg:order-1 lg:col-span-2" bodyClassName="p-0">
        <DataState loading={loading && !rows.length} error={error && !rows.length ? error : undefined} onRetry={refresh} isEmpty={!loading && !error && rows.length === 0} empty={<EmptyState title="No automations yet" hint="Create an event-driven rule to route replies, owners, or sequence stops." />} skeleton={<LoadingRows rows={5} cols={4} />}>
          {error ? <div className="p-4"><ErrorState error={error} onRetry={refresh} /></div> : null}
          <ul className="divide-y divide-line">
            {rows.map((row) => <li key={row.id}>
              <article className="px-5 py-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="truncate text-[0.875rem] font-semibold text-ink">{row.name || "Unnamed automation"}</h3>
                      <StatusBadge status={row.enabled ? "active" : "paused"} />
                    </div>
                    <div className="mt-0.5 text-xs text-ink-muted">Trigger: {row.trigger_type || "unavailable"} · <span className="tabular-nums">v{row.version ?? "—"}</span></div>
                  </div>
                  <button type="button" aria-label={`${row.enabled ? "Pause" : "Enable"} automation ${row.name || "unnamed"}`} disabled={busy} onClick={() => void toggle(row)} className="btn-secondary btn-sm">{row.enabled ? "Pause" : "Enable"}</button>
                  <button type="button" aria-label={`Inspect runs for ${row.name || "unnamed"}`} disabled={busy} onClick={() => void inspectRuns(row)} className="btn-ghost btn-sm">Status</button>
                  <button type="button" aria-label={`Delete automation ${row.name || "unnamed"}`} disabled={busy} onClick={() => void remove(row)} className="btn-ghost btn-sm text-rust-700 dark:text-rust-300">Delete</button>
                </div>
                <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                  <GraphTimeline graph={row.graph} trigger={row.trigger_type} />
                  <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 self-start rounded-lg border border-line bg-surface-3/50 p-3 text-xs">
                    <div><dt className="label">Next execution</dt><dd className="mt-0.5 text-ink">{row.next_execution ? new Date(row.next_execution).toLocaleString() : "—"}</dd></div>
                    <div><dt className="label">Runs</dt><dd className="mt-0.5 font-semibold tabular-nums text-ink">{row.run_count ?? row.runs ?? "—"}</dd></div>
                    <div><dt className="label">Last run</dt><dd className="mt-0.5">{row.last_run_status ? <StatusBadge status={row.last_run_status} /> : <span className="text-ink-faint">—</span>}</dd></div>
                    <div><dt className="label">Errors</dt><dd className={`mt-0.5 font-semibold tabular-nums ${row.error_count ? "text-rust-600 dark:text-rust-300" : "text-ink"}`}>{row.error_count ?? "—"}</dd></div>
                    <div><dt className="label">Retries</dt><dd className="mt-0.5 font-semibold tabular-nums text-ink">{row.retry_count ?? "—"}</dd></div>
                  </dl>
                </div>
                {row.last_error ? <p className="notice notice-danger mt-3 text-xs">Last error: {row.last_error}</p> : null}
              </article>
            </li>)}
          </ul>
        </DataState>
      </Panel>
    </div>
  </div>;
}
