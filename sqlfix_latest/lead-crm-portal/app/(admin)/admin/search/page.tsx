"use client";
import { setVisibleInterval, clearVisibleInterval } from "@/lib/live";
import { useEffect, useState } from "react";
import { admin } from "@/lib/admin";
import { getApiBase } from "@/lib/config";
const BASE = getApiBase();
export default function AdminSearchPage() {
  const [tenants, setTenants] = useState<any[]>([]);
  const [tenantId, setTenantId] = useState("");
  const [vertical, setVertical] = useState("medspa");
  const [geo, setGeo] = useState("Hoboken");
  const [keywords, setKeywords] = useState("med spa");
  const [offer, setOffer] = useState("lead_gen_service");
  const [limit, setLimit] = useState(15);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState("");
  const [leads, setLeads] = useState<any[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [emailOpen, setEmailOpen] = useState(false);
  const [requests, setRequests] = useState<any[]>([]);
  const [approvalReason, setApprovalReason] = useState("Approved as a controlled first batch within your monthly plan.");
  const [subj, setSubj] = useState("Proposal for {{company}}");
  const [body, setBody] = useState("<p>Hi {{owner}}, idea for {{company}} in {{city}}.</p><p>{{pitch}}</p>");
  async function loadRequests() { try { const r:any = await admin.discoveryRequests("pending_approval"); setRequests(r.requests||[]); } catch(e:any) { setMsg(e.message); } }
  useEffect(() => { admin.tenants("active").then(d => setTenants(d.tenants||[])).catch(e=>setMsg(e.message)); loadRequests(); const t=setVisibleInterval(loadRequests,15000); return ()=>clearVisibleInterval(t); }, []);
  async function findLeads() {
    if (!tenantId) return setMsg("Select client");
    setLoading(true);
    try {
      const res = await fetch(`${BASE}/crm/${tenantId}/search-run`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vertical, geo: geo.split(",").map(s=>s.trim()).filter(Boolean), keywords: keywords.split(",").map(s=>s.trim()).filter(Boolean), offer_category: offer, limit_per_location: limit }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(typeof data.detail==="string"?data.detail:JSON.stringify(data));
      setLeads(data.leads||[]); setMsg(`Found ${data.count||0} (${data.with_email||0} with email)`); setSelected(new Set());
    } catch(e:any) { setMsg(e.message); } finally { setLoading(false); }
  }
  async function sendSelected() {
    setLoading(true);
    try {
      const res = await fetch(`${BASE}/crm/${tenantId}/email/bulk`, { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({ subject:subj, body_html:body, assignment_ids:Array.from(selected)})});
      const data = await res.json();
      if (!res.ok) throw new Error(JSON.stringify(data));
      setMsg(`Sent ${data.sent||0}, failed ${data.failed||0}`); setEmailOpen(false);
    } catch(e:any) { setMsg(e.message); } finally { setLoading(false); }
  }
  return (
    <div className="page">
      <h1 className="page-title">Search & outreach</h1>
      <p className="page-sub">Client requests are queued here. Approve a controlled batch without exposing provider credits.</p>
      {requests.length > 0 && (
        <div className="card-pad space-y-3">
          <div className="flex items-center justify-between"><div className="font-semibold">Pending discovery requests</div><span className="pill">{requests.length}</span></div>
          {requests.map((r: any) => (
            <div key={r.id} className="rounded-xl border border-line p-3 space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div><b>{r.vertical}</b> · {(r.locations || []).join(", ") || "Any location"}</div>
                <span className="text-xs text-ink-muted">Requested {r.requested_leads}</span>
              </div>
              <div className="grid gap-2 sm:grid-cols-[140px_1fr_auto_auto]">
                <input className="input" type="number" min={1} max={r.requested_leads} defaultValue={Math.min(r.requested_leads, 100)} id={`approve-${r.id}`} />
                <input className="input" value={approvalReason} onChange={(e) => setApprovalReason(e.target.value)} placeholder="Reason shown to client" />
                <button aria-label="Action" className="btn-primary" onClick={async () => {
                  const n = Number((document.getElementById(`approve-${r.id}`) as HTMLInputElement)?.value || 0);
                  try { await admin.approveDiscovery(r.id, { approved_leads: n, reason: approvalReason }); setMsg(`Approved ${n} leads for ${r.vertical}.`); await loadRequests(); }
                  catch (e: any) { setMsg(e.message); }
                }}>Approve</button>
                <button aria-label="Action" className="btn-secondary" onClick={async () => {
                  const reason = prompt("Reason for rejection") || "Not approved at this time.";
                  try { await admin.rejectDiscovery(r.id, reason); setMsg("Request rejected."); await loadRequests(); }
                  catch (e: any) { setMsg(e.message); }
                }}>Reject</button>
              </div>
            </div>
          ))}
        </div>
      )}
      <p className="page-sub">Pick client → ICP → Find → email proposal</p>
      {msg && <div className="rounded-xl border border-line bg-surface-2 px-4 py-2 text-sm">{msg}</div>}
      <div className="card-pad space-y-3">
        <select className="input" value={tenantId} onChange={e=>setTenantId(e.target.value)}>
          <option value="">Client…</option>
          {tenants.map(t=><option key={t.id} value={t.id}>{t.name||t.id}</option>)}
        </select>
        <div className="grid gap-2 sm:grid-cols-2">
          <input className="input" value={vertical} onChange={e=>setVertical(e.target.value)} placeholder="vertical" />
          <input className="input" value={geo} onChange={e=>setGeo(e.target.value)} placeholder="geo" />
          <input className="input sm:col-span-2" value={keywords} onChange={e=>setKeywords(e.target.value)} placeholder="keywords" />
          <input className="input" value={offer} onChange={e=>setOffer(e.target.value)} placeholder="offer_category" />
          <input className="input" type="number" value={limit} onChange={e=>setLimit(Number(e.target.value))} />
        </div>
        <button aria-label="Action" className="btn-primary" disabled={loading} onClick={findLeads}>{loading?"Working…":"Find leads"}</button>
      </div>
      {leads.length>0 && (
        <div className="card-pad flex gap-2 flex-wrap">
          <button aria-label="Action" className="btn-secondary" onClick={()=>setSelected(new Set(leads.filter(l=>l.email).map(l=>l.assignment_id)))}>Select with email</button>
          <button aria-label="Action" className="btn-primary" disabled={!selected.size} onClick={()=>setEmailOpen(true)}>Email proposal ({selected.size})</button>
        </div>
      )}
      <div className="space-y-2">
        {leads.map(l=>(
          <label key={l.assignment_id} className="card-pad flex gap-3 text-sm">
            <input type="checkbox" checked={selected.has(l.assignment_id)} onChange={()=>{const n=new Set(selected); n.has(l.assignment_id)?n.delete(l.assignment_id):n.add(l.assignment_id); setSelected(n);}} />
            <div><div className="font-semibold">{l.name} · {l.tier}</div><div className="text-ink-muted">{l.email||"no email"} · {l.city}</div></div>
          </label>
        ))}
      </div>
      {emailOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="card-pad w-full max-w-lg space-y-3">
            <input className="input" value={subj} onChange={e=>setSubj(e.target.value)} />
            <textarea className="input min-h-[120px] font-mono text-xs" value={body} onChange={e=>setBody(e.target.value)} />
            <button aria-label="Send" className="btn-primary" onClick={sendSelected}>Send</button>
            <button aria-label="Action" className="btn-ghost" onClick={()=>setEmailOpen(false)}>Close</button>
          </div>
        </div>
      )}
    </div>
  );
}
