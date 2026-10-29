"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { crm, type DashboardResponse } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { PageHeader, Notice, Panel, Badge, type Tone } from "@/components/ui/Primitives";
import { DataState, EmptyState, LoadingRows } from "@/components/ui/States";
import { Icon } from "@/components/ui/Icon";

const PRIORITY_TONE: Record<string, Tone> = { urgent: "danger", high: "warn", normal: "neutral", low: "neutral" };

function dueInfo(due?: string | null): { label: string; overdue: boolean; today: boolean } {
  if (!due) return { label: "No due date", overdue: false, today: false };
  const d = new Date(due);
  if (Number.isNaN(d.getTime())) return { label: "No due date", overdue: false, today: false };
  const now = new Date();
  const today = d.toDateString() === now.toDateString();
  return { label: d.toLocaleDateString(), overdue: !today && d.getTime() < now.getTime(), today };
}

export default function ActivitiesPage() {
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [message, setMessage] = useState("");
  const load = useCallback(async () => { setLoading(true); setError(null); try { setData(await crm.dashboard(getTenantId())); } catch (caught) { setError(caught); } finally { setLoading(false); } }, []);
  useEffect(() => { void load(); }, [load]);
  useCrmChange(() => { void load(); }, { tables: ["lead_activities", "activities", "tasks", "lead_assignments"] });
  async function complete(taskId: string) { setBusyId(taskId); setMessage(""); try { await crm.completeTask(getTenantId(), taskId); setMessage("Task completed."); await load(); } catch (caught) { setMessage(caught instanceof Error ? caught.message : "Could not complete task."); } finally { setBusyId(null); } }
  const tasks = data?.tasks_due || [], activity = data?.recent_activity || [];
  const overdueCount = tasks.filter((t: any) => dueInfo(t.due_at).overdue).length;

  return <div className="page">
    <PageHeader eyebrow="Engage" title="Tasks & activity" description="Follow-ups and recent workspace activity" actions={<Link href="/leads" className="btn-secondary">Open lead queue<Icon name="arrow" size={14} /></Link>} meta={data ? <><span className="tabular-nums">{tasks.length} open tasks</span>{overdueCount ? <span className="tabular-nums text-rust-600 dark:text-rust-300">{overdueCount} overdue</span> : null}<span className="tabular-nums">{activity.length} recent events</span></> : undefined} />
    {message ? <Notice tone={message.includes("Could not") ? "danger" : "success"}>{message}</Notice> : null}
    <DataState loading={loading} error={error} onRetry={() => void load()} isEmpty={!data} skeleton={<div className="grid gap-4 lg:grid-cols-2"><div className="card"><LoadingRows rows={4} cols={3} /></div><div className="card"><LoadingRows rows={4} cols={2} /></div></div>}>
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Open tasks" description="Due follow-ups, oldest first as returned by the API." bodyClassName="p-0" actions={<span className="badge-neutral tabular-nums">{tasks.length}</span>}>
          <ul className="divide-y divide-line">{tasks.length === 0 ? <li><EmptyState title="No open tasks" hint="Follow-ups will appear here when the workspace has work due." icon={<Icon name="check" size={18} />} /></li> : tasks.map((t: any) => {
            const due = dueInfo(t.due_at);
            const priority = String(t.priority || "normal").toLowerCase();
            return <li key={t.id} className="flex items-center gap-3 px-5 py-3">
              <span className={`h-2 w-2 shrink-0 rounded-full ${due.overdue ? "bg-rust-500" : due.today ? "bg-amber-500" : "bg-line-strong"}`} aria-hidden="true" />
              <div className="min-w-0 flex-1">
                {t.assignment_id ? <Link href={`/leads/${t.assignment_id}`} className="block truncate text-[0.8125rem] font-medium text-ink hover:text-brand-700 dark:hover:text-brand-300">{t.title}</Link> : <span className="block truncate text-[0.8125rem] font-medium text-ink-muted">{t.title}</span>}
                <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-ink-faint">
                  <span className={`inline-flex items-center gap-1 tabular-nums ${due.overdue ? "font-medium text-rust-600 dark:text-rust-300" : due.today ? "font-medium text-amber-700 dark:text-amber-300" : ""}`}><Icon name="clock" size={12} />{due.label}{due.overdue ? " · overdue" : due.today ? " · today" : ""}</span>
                  <Badge tone={PRIORITY_TONE[priority] || "neutral"} className="capitalize">{t.priority || "normal"}</Badge>
                </div>
              </div>
              <button type="button" aria-label={`Complete task ${t.title || ""}`.trim()} onClick={() => void complete(t.id)} disabled={busyId === t.id} className="btn-secondary btn-sm shrink-0">{busyId === t.id ? "…" : <><Icon name="check" size={12} />Complete</>}</button>
            </li>;
          })}</ul>
        </Panel>
        <Panel title="Recent activity" description="Latest events across leads in this workspace.">
          {activity.length === 0 ? <EmptyState title="No activity yet" icon={<Icon name="activity" size={18} />} /> : <ol className="relative">{activity.map((a: any, i: number) => <li key={a.id} className="relative flex gap-3 pb-4 last:pb-0">
            {i < activity.length - 1 ? <span className="absolute left-[4.5px] top-3.5 h-full w-px bg-line" aria-hidden="true" /> : null}
            <span className="relative mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full border-2 border-surface-2 bg-brand-600 ring-1 ring-line dark:bg-brand-300" aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <div className="flex items-start justify-between gap-3">
                {a.assignment_id ? <Link href={`/leads/${a.assignment_id}`} className="min-w-0 truncate text-[0.8125rem] text-ink hover:text-brand-700 dark:hover:text-brand-300">{a.title || "—"}</Link> : <span className="min-w-0 truncate text-[0.8125rem] text-ink">{a.title || "—"}</span>}
                <time dateTime={a.created_at || undefined} className="shrink-0 text-xs tabular-nums text-ink-faint">{a.created_at ? new Date(a.created_at).toLocaleString() : "Date unavailable"}</time>
              </div>
              <div className="mt-1"><Badge className="capitalize">{String(a.activity_type || "activity").replace(/_/g, " ")}</Badge></div>
            </div>
          </li>)}</ol>}
        </Panel>
      </div>
    </DataState>
  </div>;
}
