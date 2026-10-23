"use client";

import { useEffect, useMemo, useState } from "react";
import { crm } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";

const TRIGGERS = [
  ["email.reply_received", "Reply received"],
  ["lead.assigned", "Lead assigned"],
  ["deal.stage_changed", "Deal stage changed"],
  ["lead.score_changed", "Lead score changed"],
  ["lead.quality_gate_passed", "Lead passed quality gate"],
] as const;
const TEMPLATES = [
  { name: "Hot reply → owner + deal", trigger: "email.reply_received", graph: { trigger: { event: "email.reply_received", conditions: [{ field: "reply_score", op: "gte", value: 75 }] }, actions: [{ type: "escalate_owner", params: { title: "Hot reply — owner action required" } }, { type: "notify_owner", params: { message: "A high-intent reply needs owner attention." } }] } },
  { name: "Opt-out → stop outreach", trigger: "email.reply_received", graph: { trigger: { event: "email.reply_received", conditions: [{ field: "category", op: "eq", value: "opt_out" }] }, actions: [{ type: "stop_sequence" }] } },
  { name: "Positive reply → owner follow-up", trigger: "email.reply_received", graph: { trigger: { event: "email.reply_received", conditions: [{ field: "reply_score", op: "gte", value: 45 }] }, actions: [{ type: "create_task", params: { title: "Follow up on interested reply", priority: "high" } }, { type: "notify_owner", params: { message: "A prospect replied with buying/interested intent." } }] } },
];

export default function AutomationsPage() {
  const [rows, setRows] = useState<any[]>([]); const [name, setName] = useState(""); const [trigger, setTrigger] = useState("email.reply_received");
  const [template, setTemplate] = useState(0); const [enabled, setEnabled] = useState(true); const [busy, setBusy] = useState(false); const [msg, setMsg] = useState("");
  const tid = useMemo(() => getTenantId(), []);
  const load = async () => { try { const r:any = await crm.automations(tid); setRows(r.automations || []); } catch(e:any) { setMsg(e.message || "Could not load automations"); } };
  useEffect(() => { load(); }, []);
  useCrmChange(load, { tables: ["workflow_definitions", "workflow_runs", "automation_events", "email_replies", "lead_assignments"] });
  async function create() { const t = TEMPLATES[template]; setBusy(true); setMsg(""); try { await crm.createAutomation(tid, { name: name.trim() || t.name, trigger_type: trigger, enabled, graph: { ...t.graph, trigger: { ...(t.graph as any).trigger, event: trigger } } }); setName(""); setMsg("Automation created"); await load(); } catch(e:any) { setMsg(e.message || "Could not create automation"); } finally { setBusy(false); } }
  async function toggle(r:any) { try { await crm.updateAutomation(tid, r.id, { name:r.name, description:r.description||"", trigger_type:r.trigger_type, graph:r.graph, enabled:!r.enabled }); await load(); } catch(e:any) { setMsg(e.message || "Update failed"); } }
  return <div className="page">
    <header><h1 className="page-title">Automations</h1><p className="page-sub">Event-driven outreach and revenue actions. Replies are read, classified, scored, and routed asynchronously.</p></header>
    <div className="grid gap-4 lg:grid-cols-[1fr_1.2fr]">
      <section className="card-pad space-y-4"><h2 className="text-sm font-semibold text-ink">Create automation</h2><input className="input" placeholder="Automation name" value={name} onChange={e=>setName(e.target.value)}/><select className="input" value={trigger} onChange={e=>setTrigger(e.target.value)}>{TRIGGERS.map(x=><option key={x[0]} value={x[0]}>{x[1]}</option>)}</select><select className="input" value={template} onChange={e=>setTemplate(Number(e.target.value))}>{TEMPLATES.map((x,i)=><option key={x.name} value={i}>{x.name}</option>)}</select><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={enabled} onChange={e=>setEnabled(e.target.checked)}/> Enable immediately</label><button aria-label="Action" disabled={busy} onClick={create} className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{busy?"Creating…":"Create automation"}</button>{msg&&<p className="text-sm text-ink-muted">{msg}</p>}</section>
      <section className="card-pad"><div className="mb-3 flex items-center justify-between"><h2 className="text-sm font-semibold text-ink">Live automations</h2><span className="text-xs text-ink-faint">Async queue · tenant isolated</span></div><div className="space-y-2">{rows.map(r=><div key={r.id} className="rounded-xl border border-line p-3"><div className="flex items-center justify-between gap-3"><div><div className="text-sm font-semibold text-ink">{r.name}</div><div className="mt-1 text-xs text-ink-muted">{r.trigger_type} · v{r.version}</div></div><button aria-label="Action" onClick={()=>toggle(r)} className={`rounded-lg px-3 py-1.5 text-xs font-medium ${r.enabled?"bg-brand-500/15 text-brand-700 dark:text-brand-200":"border border-line text-ink-muted"}`}>{r.enabled?"Enabled":"Paused"}</button></div></div>)}{rows.length===0&&<div className="py-10 text-center text-sm text-ink-faint">No automations yet</div>}</div></section>
    </div>
  </div>;
}
