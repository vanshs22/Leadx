"use client";

import { useEffect, useMemo, useState } from "react";
import { crmAgents } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";

const inputDefaults: Record<string, any> = {
  prospecting: { industry: "", target_new_leads: 25, max_assignments: 25, deliver: true },
  enrichment: { lead_ids: [] }, research: { lead_ids: [] }, signal: { lead_ids: [] },
  qualification: { lead_ids: [], offer: "" }, outreach: { lead_ids: [], offer: "" },
  crm: { days: 1 }, deal: { days: 30 }, revenue: { days: 90 }, learning: {},
};

export default function AgentsPage() {
  const [agents, setAgents] = useState<any[]>([]);
  const [runs, setRuns] = useState<any[]>([]);
  const [selected, setSelected] = useState("prospecting");
  const [task, setTask] = useState("");
  const [raw, setRaw] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const tid = getTenantId();

  const current = useMemo(() => agents.find(a => a.id === selected), [agents, selected]);

  async function refresh() {
    if (!tid) return;
    try {
      const [a, r] = await Promise.all([crmAgents.list(tid), crmAgents.runs(tid, 40)]);
      setAgents(a.agents || []); setRuns(r.runs || []);
    } catch (e: any) { setMessage(e?.message || "Could not load agents"); }
  }
  useEffect(() => { void refresh(); }, [tid]);
  useCrmChange(() => { void refresh(); }, { tables: ["agent_runs", "jobs", "lead_assignments", "lead_outcome_events"] });

  async function run() {
    if (!tid) return;
    setBusy(true); setMessage("");
    try {
      let input = inputDefaults[selected] || {};
      if (raw.trim()) input = JSON.parse(raw);
      const r = await crmAgents.run(tid, selected, task, input);
      setMessage(`Queued ${current?.name || selected}. Job ${String(r.job_id || "").slice(0, 8)}…`);
      setTask(""); await refresh();
    } catch (e: any) { setMessage(e?.message || "Agent could not be queued"); }
    finally { setBusy(false); }
  }

  return <div className="page space-y-5">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div><div className="label text-brand-600 dark:text-brand-300">LeadX Agent OS</div><h1 className="page-title mt-1">Revenue agents that work, remember and learn.</h1><p className="page-sub">Every run is durable, tenant-scoped and executed through the existing LeadX layers.</p></div>
      <button aria-label="Refresh" className="btn-secondary" onClick={refresh}>Refresh</button>
    </header>

    <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {agents.map(a => <button aria-label="Action" key={a.id} onClick={() => setSelected(a.id)} className={`text-left rounded-2xl border p-4 transition ${selected === a.id ? "border-brand-400 bg-brand-50/70 dark:border-brand-500/50 dark:bg-brand-500/10" : "border-line bg-surface-2 hover:border-brand-300"}`}>
        <div className="flex items-center justify-between"><span className="font-semibold text-ink">{a.name}</span><span className="rounded-full bg-brand-500/10 px-2 py-1 text-[10px] font-bold text-brand-700 dark:text-brand-300">AGENT</span></div>
        <p className="mt-2 text-xs leading-5 text-ink-muted">{a.description}</p>
        <div className="mt-3 flex flex-wrap gap-1.5">{(a.capabilities || []).slice(0,4).map((c: string) => <span key={c} className="rounded-md border border-line px-2 py-1 text-[10px] text-ink-faint">{c}</span>)}</div>
      </button>)}
    </section>

    <section className="grid gap-5 lg:grid-cols-[1fr_1.15fr]">
      <div className="premium-card p-5">
        <div className="label">Run an agent</div>
        <h2 className="mt-1 text-lg font-semibold text-ink">{current?.name || selected}</h2>
        <p className="mt-1 text-xs text-ink-muted">{current?.description}</p>
        <label className="mt-5 block text-xs font-semibold text-ink">Task</label>
        <textarea value={task} onChange={e => setTask(e.target.value)} className="mt-2 min-h-24 w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-brand-400" placeholder="Example: Find 25 high-fit prospects for our offer…" />
        <label className="mt-4 block text-xs font-semibold text-ink">Advanced input JSON (optional)</label>
        <textarea value={raw} onChange={e => setRaw(e.target.value)} className="mt-2 min-h-28 w-full rounded-xl border border-line bg-surface px-3 py-2 font-mono text-xs outline-none focus:border-brand-400" placeholder={JSON.stringify(inputDefaults[selected] || {}, null, 2)} />
        <button aria-label="Action" disabled={busy || !tid} onClick={run} className="btn-primary mt-4 w-full">{busy ? "Queueing…" : "Run agent"}</button>
        {message && <div className="mt-3 rounded-xl border border-line bg-surface-2 p-3 text-xs text-ink-muted">{message}</div>}
      </div>

      <div className="premium-card overflow-hidden">
        <div className="border-b border-line px-5 py-4"><div className="label">Agent memory</div><h2 className="mt-1 text-lg font-semibold text-ink">Recent work</h2></div>
        <div className="divide-y divide-line">
          {runs.length === 0 && <div className="p-5 text-sm text-ink-muted">No agent runs yet.</div>}
          {runs.map(r => <div key={r.id} className="p-4">
            <div className="flex items-center justify-between gap-3"><div className="font-medium text-ink">{agents.find(a => a.id === r.agent_id)?.name || r.agent_id}</div><span className={`rounded-full px-2 py-1 text-[10px] font-bold ${r.status === "done" ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : r.status === "failed" ? "bg-red-500/10 text-red-700 dark:text-red-300" : "bg-brand-500/10 text-brand-700 dark:text-brand-300"}`}>{r.status}</span></div>
            <p className="mt-1 line-clamp-2 text-xs text-ink-muted">{r.task || "Agent task"}</p>
            {r.error && <p className="mt-2 text-xs text-red-600">{r.error}</p>}
            <div className="mt-2 text-[10px] text-ink-faint">{r.created_at ? new Date(r.created_at).toLocaleString() : ""}</div>
          </div>)}
        </div>
      </div>
    </section>
  </div>;
}
