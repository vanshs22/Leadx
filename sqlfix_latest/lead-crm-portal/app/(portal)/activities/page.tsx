"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { crm, type DashboardResponse } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { PageHeader, Notice } from "@/components/ui/Primitives";
import { DataState, EmptyState } from "@/components/ui/States";

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
  return <div className="page space-y-5"><PageHeader title="Tasks & activity" description="Follow-ups and recent workspace activity" actions={<Link href="/leads" className="btn-secondary">Open lead queue</Link>} />{message ? <Notice tone={message.includes("Could not") ? "danger" : "success"}>{message}</Notice> : null}<DataState loading={loading} error={error} onRetry={() => void load()} isEmpty={!data} skeleton={<div className="space-y-4"><div className="card-pad h-40 animate-pulse" /><div className="card-pad h-40 animate-pulse" /></div>}><section className="card-pad"><h2 className="text-sm font-semibold text-ink">Open tasks</h2><ul className="mt-3 space-y-2">{tasks.length === 0 ? <li><EmptyState title="No open tasks" hint="Follow-ups will appear here when the workspace has work due." /></li> : tasks.map((t: any) => <li key={t.id} className="flex items-center justify-between rounded-xl2 bg-surface-3/60 px-3 py-2.5 text-sm"><div className="min-w-0">{t.assignment_id ? <Link href={`/leads/${t.assignment_id}`} className="truncate text-ink hover:text-brand-600">{t.title}</Link> : <span className="text-ink-muted">{t.title}</span>}<div className="text-xs text-ink-faint">{t.due_at ? new Date(t.due_at).toLocaleDateString() : "No due date"} · {t.priority || "normal"}</div></div><button onClick={() => void complete(t.id)} disabled={busyId === t.id} className="btn-secondary shrink-0 text-xs">{busyId === t.id ? "…" : "Complete"}</button></li>)}</ul></section><section className="card-pad"><h2 className="text-sm font-semibold text-ink">Recent activity</h2><ul className="mt-3 divide-y divide-line">{activity.length === 0 ? <li><EmptyState title="No activity yet" /></li> : activity.map((a: any) => <li key={a.id} className="flex items-center justify-between gap-3 py-2.5 text-sm"><span className="flex min-w-0 items-center gap-2"><span className="pill shrink-0 bg-surface-3 text-ink-faint">{a.activity_type}</span>{a.assignment_id ? <Link href={`/leads/${a.assignment_id}`} className="truncate text-ink-muted hover:text-brand-600">{a.title || "—"}</Link> : <span className="truncate text-ink-muted">{a.title || "—"}</span>}</span><span className="shrink-0 text-xs text-ink-faint">{a.created_at ? new Date(a.created_at).toLocaleString() : "Date unavailable"}</span></li>)}</ul></section></DataState></div>;
}
