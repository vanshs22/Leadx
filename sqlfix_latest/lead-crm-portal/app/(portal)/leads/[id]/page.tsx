"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { crm, crmIntelligence, LeadDetail, Stage } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { TierBadge, ScoreBar } from "@/components/leads/TierBadge";
import { QualityChips } from "@/components/leads/QualityChips";
import { CopyButton } from "@/components/ui/CopyButton";

export default function LeadDetailPage() {
  const params = useParams();
  const id = params?.id as string;
  const [data, setData] = useState<LeadDetail | null>(null);
  const [stages, setStages] = useState<Stage[]>([]);
  const [note, setNote] = useState("");
  const [taskTitle, setTaskTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [researchBusy, setResearchBusy] = useState(false);
  const [researchResult, setResearchResult] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const [badReason, setBadReason] = useState("wrong_number");
  const [ftOn, setFtOn] = useState(false);
  const [ftChannel, setFtChannel] = useState("sms");
  const [emailOpen, setEmailOpen] = useState(false);
  const [auditUrl, setAuditUrl] = useState("");
  const [emailSubj, setEmailSubj] = useState("Quick idea for {{company}}");
  const [emailBody, setEmailBody] = useState(
    "<p>Hi {{owner}},</p><p>I noticed a few things about {{company}} in {{city}}.</p><p>{{pitch}}</p><p>Worth a quick chat?</p>"
  );

  const reload = useCallback(() => {
    if (!id) return;
    const tid = getTenantId();
    if (!tid) return;
    crm.lead(tid, id).then(setData).catch((e) => setMsg(e.message));
  }, [id]);

  useEffect(() => {
    reload();
    const tid = getTenantId();
    if (tid) crm.stages(tid).then(setStages).catch(() => {});
  }, [id]);

  useCrmChange(() => { void reload(); }, { tables: ["lead_assignments", "leads_global", "lead_state_history", "lead_score_snapshots", "lead_quality_gates", "lead_verification_observations", "lead_outcome_events", "lead_activities", "activities"] });

  if (!data) {
    return <div className="page text-ink-faint">{msg || "Loading lead…"}</div>;
  }

  const l: any = data.lead || data;
  const phone = l.phone_e164 || l.phone;
  const email = l.email;

  async function moveStage(stageId: string) {
    setBusy(true);
    try {
      await crm.changeStage(getTenantId(), id, stageId);
      reload();
      setMsg("Stage updated");
    } finally {
      setBusy(false);
    }
  }

  async function addNote() {
    if (!note.trim()) return;
    setBusy(true);
    try {
      await crm.addActivity(getTenantId(), id, { activity_type: "note", title: "Note", body: note });
      setNote("");
      reload();
    } finally {
      setBusy(false);
    }
  }

  async function logCall() {
    setBusy(true);
    try {
      await crm.addActivity(getTenantId(), id, {
        activity_type: "call",
        title: "Outbound call",
        meta: { outcome: "logged" },
      });
      reload();
      setMsg("Call logged");
    } finally {
      setBusy(false);
    }
  }

  async function addTask() {
    if (!taskTitle.trim()) return;
    setBusy(true);
    try {
      await crm.createTask(getTenantId(), id, { title: taskTitle, priority: "medium" });
      setTaskTitle("");
      reload();
    } finally {
      setBusy(false);
    }
  }

  async function reportBad() {
    setBusy(true);
    try {
      const r: any = await crm.reportBadLead(getTenantId(), id, badReason, "Reported from CRM");
      const creditMsg = r?.credit?.message;
      if (creditMsg) {
        setMsg(creditMsg);
      } else if (r?.credit?.applied) {
        setMsg("Bad lead reported — 1 lead credited to your quota.");
      } else {
        setMsg("Bad lead reported — our team will review.");
      }
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function sendEmailToLead() {
    setBusy(true);
    try {
      const r: any = await crm.sendEmail(getTenantId(), id, {
        subject: emailSubj,
        body_html: emailBody,
      });
      setMsg(r?.ok ? `Email sent to ${r.to}` : r?.error || "Email failed");
      reload();
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function fetchMoreData() {
    setResearchBusy(true);
    setMsg("");
    try {
      const r = await crm.researchLead(getTenantId(), id);
      setResearchResult(r);
      const count = Array.isArray(r?.evidence) ? r.evidence.length : 0;
      setMsg(count ? `Research complete — ${count} evidence items merged.` : "Research complete — no additional verified data found.");
      // Refresh only this lead's state; no page navigation or full CRM reload.
      const fresh = await crm.lead(getTenantId(), id);
      setData(fresh);
    } catch (e: any) {
      setMsg(e.message || "Deep research failed");
    } finally {
      setResearchBusy(false);
    }
  }

  async function generateAudit() {
    setBusy(true);
    try {
      const r = await crmIntelligence.createAudit(getTenantId(), {
        lead_id: id,
        title: `${l.name || "Prospect"} — Prospect Audit`,
        sections: {
          company: { name: l.name, city: l.city, state: l.state, website: l.website },
          opportunity: (data as any).why_this_lead || {},
          contactability: { email, phone, owner: l.owner_name || l.decision_maker_name },
          signals: { rating: l.google_rating, reviews: l.review_count, booking: l.has_booking, sources: l.sources_hit },
          pitch: l.pitch_line || l.talking_points,
        },
      });
      setAuditUrl(r.share_url || "");
      setMsg("Prospect audit created");
    } catch (e: any) { setMsg(e.message); }
    finally { setBusy(false); }
  }

  async function sendFirstTouch() {
    if (!ftOn) {
      setMsg("Turn on the first-touch toggle, then Send once");
      return;
    }
    setBusy(true);
    try {
      const r: any = await crm.firstTouch(getTenantId(), id, ftChannel);
      if (r?.skipped === "already_sent") setMsg("Already sent");
      else if (r?.ok) setMsg(`First touch sent via ${r.channel || ftChannel}`);
      else setMsg(r?.error || "Failed");
      setFtOn(false);
      reload();
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <Link href="/leads" className="text-sm text-ink-muted hover:text-brand-600">
        ← Back to leads
      </Link>

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="page-title">{l.name || l.company_name || "Lead"}</h1>
            <TierBadge tier={l.tier} />
          </div>
          <p className="page-sub">{[l.city, l.state].filter(Boolean).join(" · ")}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {phone && (
            <a href={`tel:${phone}`} className="btn-success" onClick={() => logCall()}>
              Call now
            </a>
          )}
          {email && (
            <a href={`mailto:${email}`} className="btn-primary">
              Email
            </a>
          )}
          {l.website && (
            <a href={l.website} target="_blank" rel="noreferrer" className="btn-secondary">
              Website ↗
            </a>
          )}
          <button aria-label="Action" type="button" className="btn-primary" disabled={busy || researchBusy} onClick={fetchMoreData}>
            {researchBusy ? "Researching…" : "✦ Fetch more data"}
          </button>
          <button aria-label="Prospect audit" type="button" className="btn-secondary" disabled={busy} onClick={generateAudit}>Prospect audit</button>
        </div>
      </header>

      {msg && (
        <div className="rounded-xl border border-line bg-surface-2 px-4 py-2.5 text-sm text-ink-muted">{msg}</div>
      )}
      {auditUrl && (
        <div className="data-card flex flex-wrap items-center justify-between gap-3 p-4">
          <div><div className="label">Shareable sales artifact</div><div className="mt-1 text-sm text-ink">Prospect audit ready to send to the business.</div></div>
          <a className="btn-primary" href={auditUrl} target="_blank" rel="noreferrer">Open audit ↗</a>
        </div>
      )}

      {researchResult && (
        <div className="card border-brand-200/80 bg-brand-50/40 p-5 dark:border-brand-500/20 dark:bg-brand-500/10">
          <div className="flex items-center justify-between gap-3">
            <div><div className="label text-brand-700 dark:text-brand-300">Research evidence</div><p className="mt-1 text-sm text-ink-muted">LeadX merged new facts without replacing existing verified data.</p></div>
            <span className="pill bg-brand-100 text-brand-800 dark:bg-brand-500/20 dark:text-brand-200">{(researchResult.sources || []).join(" · ") || "no new source"}</span>
          </div>
          {!!(researchResult.evidence || []).length && (
            <div className="mt-4 grid gap-2 md:grid-cols-2">
              {(researchResult.evidence || []).slice(0, 8).map((e: any, i: number) => (
                <a key={i} href={e.url || "#"} target="_blank" rel="noreferrer" className="rounded-xl border border-line bg-surface-2 p-3 transition hover:border-brand-300 hover:shadow-sm">
                  <div className="text-xs font-semibold text-ink line-clamp-1">{e.title || "Source"}</div>
                  <div className="mt-1 text-[11px] leading-4 text-ink-muted line-clamp-3">{e.snippet || e.url}</div>
                </a>
              ))}
            </div>
          )}
        </div>
      )}

      {l.pitch_line && (
        <div className="card border-brand-200 bg-brand-50 p-5 dark:border-brand-500/20 dark:bg-brand-500/10">
          <div className="label text-brand-800 dark:text-brand-300">Talk track</div>
          <p className="mt-2 font-medium text-ink">{l.talking_points || l.pitch_line}</p>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="card-pad space-y-4">
          <h2 className="text-sm font-semibold">Contact</h2>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between"><span className="text-ink-faint">Phone</span><span>{phone || "—"} <CopyButton value={phone} /></span></div>
            <div className="flex justify-between"><span className="text-ink-faint">Email</span><span>{email || "—"} <CopyButton value={email} /></span></div>
            <div className="flex justify-between"><span className="text-ink-faint">Owner</span><span>{l.owner_name || l.decision_maker_name || "—"}</span></div>
          </div>
          {(l.review_snippets || l.reviews_sample) && (
            <div className="mt-3 rounded-xl border border-line p-3 text-xs">
              <div className="mb-1 font-semibold uppercase text-ink-faint">Reviews signal</div>
              <p className="text-ink-muted">
                Rating {l.google_rating ?? l.rating ?? "—"} · {l.review_count ?? l.reviews ?? "—"} reviews
              </p>
              <ul className="mt-2 space-y-1 text-ink-muted">
                {(l.review_snippets || l.reviews_sample || []).slice(0, 5).map((r: any, i: number) => (
                  <li key={i}>“{typeof r === "string" ? r : r.text || r.snippet || JSON.stringify(r)}”</li>
                ))}
              </ul>
            </div>
          )}
          <QualityChips tier={l.tier} score={l.score ?? l.total_score} intent={l.intent_score} sources={l.sources_hit} talkingPoints={l.talking_points} pitch={l.pitch_line} />
          {typeof (l.score ?? l.total_score) === "number" && <ScoreBar score={l.score ?? l.total_score} />}

          {/* Why this lead — score transparency */}
          {(data as any).why_this_lead?.bullets?.length > 0 && (
            <div className="mt-4 rounded-xl border border-line bg-surface-2 p-4">
              <div className="label mb-2">Why this lead</div>
              <ul className="space-y-1.5 text-sm text-ink-muted">
                {((data as any).why_this_lead.bullets as string[]).map((b, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="text-brand-600 shrink-0">·</span>
                    <span>{b}</span>
                  </li>
                ))}
              </ul>
              {(data as any).why_this_lead.tier && (
                <p className="mt-3 text-xs text-ink-faint">
                  Tier {(data as any).why_this_lead.tier}
                  {(data as any).why_this_lead.total_score != null &&
                    ` · Score ${(data as any).why_this_lead.total_score}`}
                </p>
              )}
            </div>
          )}
        </div>

        <div className="card-pad space-y-5 lg:col-span-2">
          <div>
            <h2 className="text-sm font-semibold">Update stage</h2>
            <div className="mt-2 flex flex-wrap gap-2">
              {stages.map((s: any) => (
                <button aria-label="Action" key={s.id} disabled={busy} onClick={() => moveStage(s.id)} className="btn-secondary">
                  {s.name || s.slug}
                </button>
              ))}
            </div>
          </div>
          <div>
            <h3 className="text-sm font-semibold">Log a note</h3>
            <textarea className="input mt-2 min-h-[80px]" value={note} onChange={(e) => setNote(e.target.value)} />
            <button aria-label="Save note" disabled={busy} onClick={addNote} className="btn-primary mt-2">Save note</button>
          </div>
          <div>
            <h3 className="text-sm font-semibold">Follow-up task</h3>
            <div className="mt-2 flex gap-2">
              <input className="input flex-1" value={taskTitle} onChange={(e) => setTaskTitle(e.target.value)} />
              <button aria-label="Add" disabled={busy} onClick={addTask} className="btn-secondary">Add</button>
            </div>
          </div>

          <div className="border-t border-line pt-4 space-y-2">
            <div className="flex justify-between">
              <h3 className="text-sm font-semibold">Email this lead</h3>
              <button aria-label="Action" type="button" className="btn-ghost text-xs" onClick={() => setEmailOpen(!emailOpen)}>
                {emailOpen ? "Hide" : "Compose"}
              </button>
            </div>
            {emailOpen && (
              <div className="space-y-2">
                <input className="input" value={emailSubj} onChange={(e) => setEmailSubj(e.target.value)} />
                <textarea className="input min-h-[120px] font-mono text-xs" value={emailBody} onChange={(e) => setEmailBody(e.target.value)} />
                <button aria-label="Send email" disabled={busy} onClick={sendEmailToLead} className="btn-primary">Send email</button>
              </div>
            )}
          </div>

          <div className="border-t border-line pt-4 space-y-2">
            <h3 className="text-sm font-semibold">First touch (manual)</h3>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={ftOn} onChange={(e) => setFtOn(e.target.checked)} />
              Enable send
            </label>
            <select className="input max-w-[120px]" value={ftChannel} onChange={(e) => setFtChannel(e.target.value)}>
              <option value="sms">SMS</option>
              <option value="voice">Voice</option>
            </select>
            <button aria-label="Send once" disabled={busy || !ftOn} onClick={sendFirstTouch} className="btn-primary">Send once</button>
          </div>

          <div className="border-t border-line pt-4">
            <h3 className="text-sm font-semibold text-rust-700 dark:text-rust-300">Report bad lead</h3>
            <div className="mt-2 flex gap-2">
              <select className="input max-w-[180px]" value={badReason} onChange={(e) => setBadReason(e.target.value)}>
                <option value="wrong_number">Wrong number</option>
                <option value="email_bounce">Email bounce</option>
                <option value="closed">Closed</option>
                <option value="duplicate">Duplicate</option>
                <option value="other">Other</option>
              </select>
              <button aria-label="Report" disabled={busy} onClick={reportBad} className="btn-danger">Report</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
