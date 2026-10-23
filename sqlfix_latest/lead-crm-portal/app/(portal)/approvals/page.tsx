"use client";

import { useEffect, useState } from "react";
import { crmApprovals } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";

export default function ApprovalsPage() {
  const [items,setItems]=useState<any[]>([]); const [auto,setAuto]=useState(false); const [msg,setMsg]=useState(""); const [busy,setBusy]=useState(false);
  const tid=getTenantId();
  async function refresh(){ if(!tid)return; try{ const r=await crmApprovals.list(tid); setItems(r.items||[]); setAuto(Boolean(r.outreach_auto_approve)); }catch(e:any){setMsg(e?.message||"Could not load approvals");} }
  useEffect(()=>{void refresh()},[tid]);
  useCrmChange(() => { void refresh(); }, { tables: ["approval_inbox", "agent_approvals", "email_replies", "lead_assignments"] });
  async function decide(id:string,approved:boolean){setBusy(true);try{await crmApprovals.decide(tid,id,approved);await refresh()}catch(e:any){setMsg(e?.message||"Could not update approval")}finally{setBusy(false)}}
  async function toggle(){setBusy(true);try{await crmApprovals.settings(tid,!auto);setAuto(!auto)}catch(e:any){setMsg(e?.message||"Could not update setting")}finally{setBusy(false)}}
  return <div className="page space-y-5">
    <header className="flex flex-wrap items-end justify-between gap-4"><div><div className="label text-brand-600 dark:text-brand-300">Agent Governance</div><h1 className="page-title mt-1">Approval inbox</h1><p className="page-sub">Outbound drafts stay blocked until a workspace member approves them.</p></div><button aria-label="Refresh" className="btn-secondary" onClick={refresh}>Refresh</button></header>
    <section className="premium-card p-5 flex flex-wrap items-center justify-between gap-4"><div><div className="font-semibold text-ink">Outreach auto-approve</div><p className="text-xs text-ink-muted mt-1">When enabled, outreach can move directly into the configured execution path.</p></div><button aria-label="Action" disabled={busy} onClick={toggle} className={auto?"btn-primary":"btn-secondary"}>{auto?"Enabled":"Disabled"}</button></section>
    {msg&&<div className="rounded-xl border border-line bg-surface-2 p-3 text-sm text-ink-muted">{msg}</div>}
    <section className="premium-card overflow-hidden"><div className="border-b border-line px-5 py-4"><div className="label">Needs approval</div><h2 className="mt-1 text-lg font-semibold text-ink">{items.length} outbound draft{items.length===1?"":"s"}</h2></div><div className="divide-y divide-line">
      {items.length===0&&<div className="p-6 text-sm text-ink-muted">Nothing is waiting for review.</div>}
      {items.map(item=><div key={item.id} className="p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="font-semibold text-ink">Outreach draft</div><div className="mt-1 text-xs text-ink-faint">Lead {item.lead_id||"—"} · {item.created_at?new Date(item.created_at).toLocaleString():""}</div></div><span className="rounded-full bg-amber-500/10 px-2 py-1 text-[10px] font-bold text-amber-700 dark:text-amber-300">NEEDS APPROVAL</span></div><pre className="mt-4 max-h-64 overflow-auto rounded-xl border border-line bg-surface-2 p-4 whitespace-pre-wrap text-xs text-ink-muted">{JSON.stringify(item.payload||{},null,2)}</pre><div className="mt-4 flex gap-2"><button aria-label="Action" disabled={busy} onClick={()=>decide(item.id,true)} className="btn-primary">Approve</button><button aria-label="Action" disabled={busy} onClick={()=>decide(item.id,false)} className="btn-secondary">Reject</button></div></div>)}
    </div></section>
  </div>
}
