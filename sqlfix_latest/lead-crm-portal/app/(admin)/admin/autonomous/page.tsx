"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { autonomousCampaigns, admin, adminExtras, type SequenceStepInput } from "@/lib/admin";

const starter: SequenceStepInput[] = [
  { step_number: 1, delay_days: 0, subject: "Quick idea for {{company}}", body_html: "<p>Hi {{first_name}},</p><p>I came across {{company}} and noticed {{pitch}}.</p><p>Would it be useful to compare notes for 10 minutes?</p>" },
  { step_number: 2, delay_days: 3, subject: "Re: {{company}}", body_html: "<p>Hi {{first_name}},</p><p>Following up in case my note got buried. Happy to send a short example of what this could look like for {{company}}.</p>" },
  { step_number: 3, delay_days: 7, subject: "Closing the loop", body_html: "<p>Hi {{first_name}},</p><p>I’ll close the loop here. If this becomes relevant later, just reply and I’ll pick it back up.</p>" },
];

const csv = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

export default function AutonomousGrowthPage() {
  const [data, setData] = useState<any>(null);
  const [icps, setIcps] = useState<any[]>([]);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [steps, setSteps] = useState<SequenceStepInput[]>(starter);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [form, setForm] = useState({
    name: "US Agency Growth Engine",
    icp_id: "",
    locations: "Texas, Florida, New York, California",
    queries: "digital marketing agency, web design agency, SEO agency, AI automation agency",
    offer: "Lead generation and AI automation services",
    goal: "Book qualified discovery calls",
    daily_new_lead_limit: 25,
    daily_email_limit: 25,
    schedule_hours: 24,
    timezone: "America/New_York",
    score_threshold: 70,
  });

  const load = useCallback(async () => {
    try {
      const r = await autonomousCampaigns.list();
      setData(r);
      if (r.operator_tenant_id) {
        const i = await admin.icps(r.operator_tenant_id).catch(() => null);
        setIcps(i?.icps || i?.rows || []);
      }
      setError("");
    } catch (e: any) { setError(e.message); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const campaigns = data?.campaigns || [];
  const active = campaigns.filter((c: any) => c.status === "active").length;
  const todaySent = Object.values(data?.daily || {}).reduce((n: number, x: any) => n + Number(x?.emails_sent || 0), 0);
  const todayFound = Object.values(data?.daily || {}).reduce((n: number, x: any) => n + Number(x?.leads_discovered || 0), 0);

  function moveStep(from: number, to: number) {
    if (to < 0 || to >= steps.length || from === to) return;
    const copy = [...steps]; const [item] = copy.splice(from, 1); copy.splice(to, 0, item);
    setSteps(copy.map((s, i) => ({ ...s, step_number: i + 1 })));
  }

  async function create() {
    setBusy(true); setMsg(""); setError("");
    try {
      let sequenceId: string | undefined;
      if (steps.length) {
        const seq: any = await adminExtras.createSequence({ tenant_id: data.operator_tenant_id, name: `${form.name} sequence`, steps: steps.map((s, i) => ({ ...s, step_number: i + 1 })) });
        sequenceId = seq?.id || seq?.sequence?.id;
      }
      const r: any = await autonomousCampaigns.create({
        ...form,
        icp_id: form.icp_id || null,
        sequence_id: sequenceId || null,
        locations: csv(form.locations), queries: csv(form.queries),
        generate_ai_copy: true, auto_followup: true, stop_on_reply: true, stop_on_optout: true,
        require_verified_email: true,
      });
      setMsg(`Campaign created. ${r.campaign?.status === "active" ? "It is running." : "Activate it when ready."}`);
      await load();
    } catch (e: any) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function setStatus(id: string, status: string) {
    setBusy(true); setMsg("");
    try { await autonomousCampaigns.update(id, { status }); setMsg(status === "active" ? "Campaign is live — LeadX will handle discovery and outreach." : `Campaign ${status}.`); await load(); }
    catch (e: any) { setError(e.message); } finally { setBusy(false); }
  }

  async function runNow(id: string) {
    setBusy(true); setMsg("");
    try { await autonomousCampaigns.runNow(id); setMsg("Run queued. Follow the live counters below."); await load(); }
    catch (e: any) { setError(e.message); } finally { setBusy(false); }
  }

  return (
    <div className="page">
      <header className="hero-panel p-6 md:p-8">
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div>
            <div className="section-kicker text-signal-700 dark:text-signal-300">Operator autopilot</div>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight md:text-4xl">Autonomous growth</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-ink-muted">Choose an ICP once. LeadX continuously finds qualified prospects, writes evidence-grounded personalized emails, sends within your daily budget, follows up, and stops when the prospect replies or opts out.</p>
          </div>
          <div className="rounded-2xl border border-signal-200 bg-signal-50/70 px-4 py-3 text-xs text-signal-800 dark:border-signal-500/20 dark:bg-signal-500/10 dark:text-signal-200">
            <div className="font-bold">Admin-only</div><div className="mt-1 opacity-80">Client portal workflow is unchanged.</div>
          </div>
        </div>
      </header>

      {error && <div className="card-pad border-rust-300 text-sm text-rust-700 dark:text-rust-300">{error}</div>}
      {msg && <div className="card-pad border-signal-300 text-sm text-signal-700 dark:text-signal-300">{msg}</div>}

      <section className="grid gap-3 sm:grid-cols-4">
        {[["Active campaigns", active], ["Leads found today", todayFound], ["Emails sent today", todaySent], ["Safety gates", "Reply + opt-out"]].map(([label, value]) => (
          <div className="data-card data-card-hover p-5" key={String(label)}><div className="stat-label">{label}</div><div className="mt-2 metric-number">{value}</div></div>
        ))}
      </section>

      <section className="grid gap-5 xl:grid-cols-[1.05fr_.95fr]">
        <div className="data-card p-5 md:p-6">
          <div className="flex items-center justify-between"><div><div className="section-kicker">01 · Strategy</div><h2 className="mt-1 text-lg font-semibold">Create autonomous campaign</h2></div><span className="pill bg-signal-50 text-signal-700 dark:bg-signal-500/15 dark:text-signal-300">Admin only</span></div>
          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            <label className="label">Campaign name<input className="input mt-1" value={form.name} onChange={e=>setForm({...form,name:e.target.value})}/></label>
            <label className="label">ICP<select className="input mt-1" value={form.icp_id} onChange={e=>setForm({...form,icp_id:e.target.value})}><option value="">Use locations + queries</option>{icps.map((i: any)=><option key={i.id} value={i.id}>{i.name} · {i.vertical}</option>)}</select></label>
            <label className="label sm:col-span-2">Offer<textarea className="input mt-1 min-h-20" value={form.offer} onChange={e=>setForm({...form,offer:e.target.value})}/></label>
            <label className="label sm:col-span-2">Locations<textarea className="input mt-1 min-h-16" value={form.locations} onChange={e=>setForm({...form,locations:e.target.value})}/></label>
            <label className="label sm:col-span-2">Search intent / queries<textarea className="input mt-1 min-h-16" value={form.queries} onChange={e=>setForm({...form,queries:e.target.value})}/></label>
            <label className="label">New leads / day<input type="number" min={1} max={5000} className="input mt-1" value={form.daily_new_lead_limit} onChange={e=>setForm({...form,daily_new_lead_limit:Number(e.target.value)})}/></label>
            <label className="label">Emails / day<input type="number" min={1} max={5000} className="input mt-1" value={form.daily_email_limit} onChange={e=>setForm({...form,daily_email_limit:Number(e.target.value)})}/></label>
            <label className="label">Discovery cadence<select className="input mt-1" value={form.schedule_hours} onChange={e=>setForm({...form,schedule_hours:Number(e.target.value)})}><option value={24}>Daily</option><option value={12}>Every 12h</option><option value={72}>Every 3 days</option><option value={168}>Weekly</option></select></label>
            <label className="label">Minimum score<input type="number" min={0} max={100} className="input mt-1" value={form.score_threshold} onChange={e=>setForm({...form,score_threshold:Number(e.target.value)})}/></label>
          </div>
        </div>

        <div className="data-card p-5 md:p-6">
          <div className="section-kicker">02 · Follow-up journey</div>
          <div className="mt-1 flex items-center justify-between"><h2 className="text-lg font-semibold">Drag to reorder sequence</h2><span className="text-xs text-ink-faint">AI personalizes each step</span></div>
          <div className="mt-4 space-y-2">
            {steps.map((s: SequenceStepInput,i: number)=><div key={i} draggable onDragStart={()=>setDragIndex(i)} onDragOver={e=>e.preventDefault()} onDrop={()=>{if(dragIndex!==null)moveStep(dragIndex,i);setDragIndex(null)}} className={`rounded-2xl border bg-surface-2 p-4 transition ${dragIndex===i?"border-brand-400 shadow-lift":"border-line"}`}>
              <div className="flex items-start gap-3"><div className="mt-0.5 cursor-grab rounded-xl bg-surface-3 px-2.5 py-2 text-xs font-bold text-ink-muted">↕ {i+1}</div><div className="min-w-0 flex-1"><div className="flex items-center justify-between gap-2"><input className="input h-9" value={s.subject} onChange={e=>{const x=[...steps];x[i]={...x[i],subject:e.target.value};setSteps(x)}}/><span className="ml-2 whitespace-nowrap text-[10px] font-bold uppercase tracking-wide text-ink-faint">{i===0?"Immediately":`+${s.delay_days}d`}</span></div><textarea className="input mt-2 min-h-20" value={s.body_html} onChange={e=>{const x=[...steps];x[i]={...x[i],body_html:e.target.value};setSteps(x)}}/></div></div>
            </div>)}
          </div>
          <div className="mt-4 grid grid-cols-2 gap-2"><button aria-label="Action" className="btn-secondary" onClick={()=>setSteps([...steps,{step_number:steps.length+1,delay_days:7,subject:"One last note about {{company}}",body_html:"<p>Hi {{first_name}},</p><p>Worth keeping this on your radar?</p>"}])}>+ Add step</button><button aria-label="Action" className="btn-primary" disabled={busy || !data?.operator_tenant_id} onClick={create}>{busy?"Creating…":"Create campaign"}</button></div>
        </div>
      </section>

      <section className="data-card overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4"><div><div className="section-kicker">03 · Control room</div><h2 className="mt-1 text-lg font-semibold">Campaigns</h2></div><button aria-label="Action" className="btn-secondary" disabled={busy} onClick={()=>autonomousCampaigns.tick().then(load)}>Run due check</button></div>
        <div className="divide-y divide-line/70">
          {!campaigns.length && <div className="p-8 text-sm text-ink-muted">No autonomous campaigns yet. Create one above; nothing sends until you activate it.</div>}
          {campaigns.map((c: any)=>{const d=data?.daily?.[c.id]||{};return <div key={c.id} className="p-5 hover:bg-surface-3/30"><div className="flex flex-wrap items-start justify-between gap-4"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold text-ink">{c.name}</h3><span className="pill bg-surface-3 text-ink-muted">{c.status}</span></div><p className="mt-1 text-xs text-ink-faint">{(c.locations||[]).slice(0,4).join(" · ")} · {c.daily_new_lead_limit} leads/day · {c.daily_email_limit} emails/day</p></div><div className="flex gap-2">{c.status!=="active"?<button aria-label="Action" className="btn-success" disabled={busy} onClick={()=>setStatus(c.id,"active")}>Activate</button>:<button aria-label="Action" className="btn-secondary" disabled={busy} onClick={()=>setStatus(c.id,"paused")}>Pause</button>}<button aria-label="Action" className="btn-secondary" disabled={busy} onClick={()=>runNow(c.id)}>Run now</button></div></div><div className="mt-4 grid gap-3 sm:grid-cols-5"><div className="action-card"><div className="stat-label">Found today</div><div className="mt-1 font-mono text-lg font-semibold">{d.leads_discovered||0}</div></div><div className="action-card"><div className="stat-label">Sent today</div><div className="mt-1 font-mono text-lg font-semibold">{d.emails_sent||0} / {c.daily_email_limit}</div></div><div className="action-card"><div className="stat-label">Follow-up</div><div className="mt-1 text-sm font-semibold">{c.auto_followup?"Automatic":"Off"}</div></div><div className="action-card"><div className="stat-label">Reply stop</div><div className="mt-1 text-sm font-semibold">{c.stop_on_reply?"On":"Off"}</div></div><div className="action-card"><div className="stat-label">Opt-out</div><div className="mt-1 text-sm font-semibold">{c.stop_on_optout?"Global + tenant":"Manual"}</div></div></div></div>})}
        </div>
      </section>
    </div>
  );
}
