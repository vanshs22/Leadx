"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { crm, DashboardResponse } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";

export default function ActivitiesPage() {
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [msg, setMsg] = useState("");

  function load() {
    crm.dashboard(getTenantId()).then(setData).catch((e: any) => setMsg(e.message));
  }
  useEffect(load, []);
  useCrmChange(load, { tables: ["lead_activities", "activities", "tasks", "lead_assignments"] });

  async function complete(taskId: string) {
    setBusyId(taskId);
    try {
      await crm.completeTask(getTenantId(), taskId);
      load();
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBusyId(null);
    }
  }

  const tasks = data?.tasks_due || [];
  const activity = data?.recent_activity || [];

  return (
    <div className="page">
      <header>
        <h1 className="page-title">Tasks & activity</h1>
        <p className="page-sub">Follow-ups and recent workspace activity</p>
      </header>

      {msg && <p className="text-sm text-rust-500 dark:text-rust-300">{msg}</p>}

      <section className="card-pad">
        <h2 className="text-sm font-semibold text-ink">Open tasks</h2>
        <ul className="mt-3 space-y-2">
          {tasks.length === 0 && <li className="text-sm text-ink-faint">No open tasks</li>}
          {tasks.map((t: any) => (
            <li
              key={t.id}
              className="flex items-center justify-between rounded-xl2 bg-surface-3/60 px-3 py-2.5 text-sm"
            >
              <div className="min-w-0">
                {t.assignment_id ? (
                  <Link href={`/leads/${t.assignment_id}`} className="truncate text-ink hover:text-brand-600 dark:hover:text-brand-300">
                    {t.title}
                  </Link>
                ) : (
                  <span className="text-ink-muted">{t.title}</span>
                )}
                <div className="text-xs text-ink-faint">
                  {t.due_at ? new Date(t.due_at).toLocaleDateString() : "No due date"} · {t.priority || "normal"}
                </div>
              </div>
              <button aria-label="Action"
                onClick={() => complete(t.id)}
                disabled={busyId === t.id}
                className="btn-secondary shrink-0 text-xs disabled:opacity-50"
              >
                {busyId === t.id ? "…" : "Complete"}
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="card-pad">
        <h2 className="text-sm font-semibold text-ink">Recent activity</h2>
        <ul className="mt-3 divide-y divide-line">
          {activity.length === 0 && <li className="py-2 text-sm text-ink-faint">No activity yet</li>}
          {activity.map((a: any) => (
            <li key={a.id} className="flex items-center justify-between py-2.5 text-sm">
              <span className="flex items-center gap-2 min-w-0">
                <span className="pill shrink-0 bg-surface-3 text-ink-faint">{a.activity_type}</span>
                {a.assignment_id ? (
                  <Link href={`/leads/${a.assignment_id}`} className="truncate text-ink-muted hover:text-brand-600 dark:hover:text-brand-300">
                    {a.title || "—"}
                  </Link>
                ) : (
                  <span className="truncate text-ink-muted">{a.title || "—"}</span>
                )}
              </span>
              <span className="shrink-0 text-xs text-ink-faint">
                {new Date(a.created_at).toLocaleString()}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
