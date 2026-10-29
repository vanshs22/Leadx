"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { crm, crmIntelligence, LeadDetail, Stage } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { TierBadge, ScoreBar } from "@/components/leads/TierBadge";
import { QualityChips } from "@/components/leads/QualityChips";
import { CopyButton } from "@/components/ui/CopyButton";
import { ErrorState, EmptyState, LoadingRows } from "@/components/ui/States";
import { Panel, KeyValue, Notice, StatusBadge, Badge, QuietBreadcrumbs } from "@/components/ui/Primitives";
import { Icon } from "@/components/ui/Icon";

function fmtDate(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

const ACTIVITY_ICON: Record<string, "phone" | "mail" | "compose" | "check" | "activity"> = {
  call: "phone", email: "mail", note: "compose", task: "check",
};

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
    return (
      <div className="page">
        <QuietBreadcrumbs items={[{ label: "Leads", href: "/leads" }, { label: "Lead" }]} />
        {msg ? (
          <ErrorState error={msg} onRetry={reload} />
        ) : (
          <div className="space-y-6" aria-busy="true">
            <span className="sr-only">Loading lead…</span>
            <div className="page-header"><div className="space-y-2"><div className="skeleton h-7 w-64" /><div className="skeleton h-4 w-40" /></div><div className="flex gap-2"><div className="skeleton h-9 w-24" /><div className="skeleton h-9 w-24" /></div></div>
            <div className="grid gap-4 lg:grid-cols-3">
              <div className="panel lg:col-span-2"><LoadingRows rows={6} cols={3} /></div>
              <div className="panel"><LoadingRows rows={6} cols={2} /></div>
            </div>
          </div>
        )}
      </div>
    );
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


  const name = l.name || l.company_name || "Lead";
  const score = l.score ?? l.total_score;
  const why: any = (data as any).why_this_lead;
  const activities: any[] = Array.isArray((data as any).activities) ? (data as any).activities : [];
  const tasks: any[] = Array.isArray((data as any).tasks) ? (data as any).tasks : [];
  const location = [l.city, l.state].filter(Boolean).join(" · ");
  const reviews = (l.review_snippets || l.reviews_sample || []) as any[];

  return (
    <div className="page">
      <QuietBreadcrumbs items={[{ label: "Leads", href: "/leads" }, { label: name }]} />

      <PageHeaderLocal
        title={name}
        location={location}
        badges={<>
          <TierBadge tier={l.tier} />
          {l.stage_name ? <StatusBadge status={l.stage_slug || l.stage_name} label={l.stage_name} /> : null}
          {typeof score === "number" ? <Badge tone={score >= 85 ? "accent" : "neutral"}><span className="font-medium opacity-80">Score</span><span className="tabular-nums">{score}</span></Badge> : null}
          {l.category ? <Badge tone="neutral" className="capitalize">{l.category}</Badge> : null}
        </>}
        actions={<>
          {phone && (
            <a href={`tel:${phone}`} className="btn-success" onClick={() => logCall()} aria-label={`Call ${name}`}>
              <Icon name="phone" size={14} />Call now
            </a>
          )}
          {email && (
            <a href={`mailto:${email}`} className="btn-secondary" aria-label={`Email ${name}`}>
              <Icon name="mail" size={14} />Email
            </a>
          )}
          {l.website && (
            <a href={l.website} target="_blank" rel="noreferrer" className="btn-secondary" aria-label={`Open ${name} website in a new tab`}>
              <Icon name="globe" size={14} />Website
            </a>
          )}
          <button aria-label="Prospect audit" type="button" className="btn-secondary" disabled={busy} onClick={generateAudit}><Icon name="report" size={14} />Prospect audit</button>
          <button type="button" className="btn-primary" disabled={busy || researchBusy} onClick={fetchMoreData} aria-busy={researchBusy}>
            <Icon name="spark" size={14} />{researchBusy ? "Researching…" : "Fetch more data"}
          </button>
        </>}
      />

      {msg && <Notice tone="neutral" onDismiss={() => setMsg("")}>{msg}</Notice>}
      {auditUrl && (
        <Notice tone="success" title="Prospect audit ready">
          <span className="flex flex-wrap items-center gap-3">
            Shareable sales artifact, ready to send to the business.
            <a className="btn-secondary btn-sm" href={auditUrl} target="_blank" rel="noreferrer"><Icon name="external" size={12} />Open audit</a>
          </span>
        </Notice>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        {/* ---------------- Main column ---------------- */}
        <div className="min-w-0 space-y-4 lg:col-span-2">
          {(l.pitch_line || l.talking_points || why?.bullets?.length > 0) && (
            <Panel title="Why this lead" description="Score transparency and the talk track to open with." id="lead-why">
              <div className="space-y-4">
                {(l.pitch_line || l.talking_points) && (
                  <div className="rounded-lg border border-signal-200 bg-signal-50 px-4 py-3 dark:border-signal-400/25 dark:bg-signal-400/10">
                    <div className="eyebrow text-signal-700 dark:text-signal-300">Talk track</div>
                    <p className="mt-1.5 text-[0.875rem] font-medium leading-6 text-ink">{l.talking_points || l.pitch_line}</p>
                  </div>
                )}
                {why?.bullets?.length > 0 && (
                  <ul className="space-y-2">
                    {(why.bullets as string[]).map((b, i) => (
                      <li key={i} className="flex gap-2.5 text-[0.8125rem] leading-5 text-ink-muted">
                        <Icon name="check" size={14} className="mt-0.5 shrink-0 text-emerald-700 dark:text-emerald-300" />
                        <span>{b}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {why?.tier && (
                  <p className="text-xs tabular-nums text-ink-faint">
                    Tier {why.tier}
                    {why.total_score != null && ` · Score ${why.total_score}`}
                  </p>
                )}
              </div>
            </Panel>
          )}

          {researchResult && (
            <Panel
              title="Research evidence"
              description="LeadX merged new facts without replacing existing verified data."
              actions={<Badge tone="brand">{(researchResult.sources || []).join(" · ") || "no new source"}</Badge>}
            >
              {(researchResult.evidence || []).length ? (
                <div className="grid gap-2 md:grid-cols-2">
                  {(researchResult.evidence || []).slice(0, 8).map((e: any, i: number) => (
                    <a key={i} href={e.url || "#"} target="_blank" rel="noreferrer" className="action-card block p-3" title={e.url || e.title}>
                      <div className="line-clamp-1 text-xs font-semibold text-ink">{e.title || "Source"}</div>
                      <div className="mt-1 line-clamp-3 text-[11px] leading-4 text-ink-muted">{e.snippet || e.url}</div>
                    </a>
                  ))}
                </div>
              ) : (
                <p className="text-[0.8125rem] text-ink-faint">No additional verified evidence was found.</p>
              )}
            </Panel>
          )}

          <Panel title="Engage" description="Log what happened and reach out from one place." id="lead-engage">
            <div className="space-y-5">
              <div>
                <label className="field-label" htmlFor="lead-note">Log a note</label>
                <textarea id="lead-note" className="textarea min-h-[84px]" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Spoke with the owner, follow up Tuesday…" />
                <div className="mt-2 flex justify-end">
                  <button aria-label="Save note" disabled={busy} onClick={addNote} className="btn-primary btn-sm">Save note</button>
                </div>
              </div>

              <div className="soft-divider pt-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h3 className="section-title">Email this lead</h3>
                    <p className="section-sub">Merge tags are filled from the lead record.</p>
                  </div>
                  <button type="button" className="btn-ghost btn-sm" onClick={() => setEmailOpen(!emailOpen)} aria-expanded={emailOpen} aria-controls="lead-email-compose">
                    {emailOpen ? "Hide" : "Compose"}
                  </button>
                </div>
                {emailOpen && (
                  <div id="lead-email-compose" className="mt-3 space-y-3">
                    <div>
                      <label className="field-label" htmlFor="lead-email-subject">Subject</label>
                      <input id="lead-email-subject" className="input" value={emailSubj} onChange={(e) => setEmailSubj(e.target.value)} />
                    </div>
                    <div>
                      <label className="field-label" htmlFor="lead-email-body">Body (HTML)</label>
                      <textarea id="lead-email-body" className="textarea min-h-[140px] font-mono text-xs" value={emailBody} onChange={(e) => setEmailBody(e.target.value)} />
                    </div>
                    <div className="flex justify-end">
                      <button aria-label="Send email" disabled={busy} onClick={sendEmailToLead} className="btn-primary btn-sm">Send email</button>
                    </div>
                  </div>
                )}
              </div>

              <div className="soft-divider pt-4">
                <h3 className="section-title">First touch (manual)</h3>
                <p className="section-sub">Sends one message on the chosen channel. Enable to confirm.</p>
                <div className="mt-3 flex flex-wrap items-center gap-3">
                  <label className="flex items-center gap-2 text-[0.8125rem] text-ink">
                    <input type="checkbox" className="h-4 w-4 rounded border-line-strong accent-brand-700" checked={ftOn} onChange={(e) => setFtOn(e.target.checked)} />
                    Enable send
                  </label>
                  <select className="select h-9 w-auto min-w-[120px]" value={ftChannel} onChange={(e) => setFtChannel(e.target.value)} aria-label="First touch channel">
                    <option value="sms">SMS</option>
                    <option value="voice">Voice</option>
                  </select>
                  <button aria-label="Send once" disabled={busy || !ftOn} onClick={sendFirstTouch} className="btn-primary btn-sm ml-auto">Send once</button>
                </div>
              </div>
            </div>
          </Panel>

          <Panel title="Activity" description="Calls, notes, emails and state changes on this lead." bodyClassName="p-0" id="lead-activity">
            {activities.length === 0 ? (
              <EmptyState title="No activity yet" hint="Calls, notes and emails you log here appear in this timeline." />
            ) : (
              <ol className="divide-y divide-line" aria-label="Activity timeline">
                {activities.slice(0, 50).map((a: any) => {
                  const type = String(a.activity_type || "activity").toLowerCase();
                  return (
                    <li key={a.id} className="flex gap-3 px-5 py-3">
                      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-line bg-surface-3 text-ink-muted" aria-hidden="true">
                        <Icon name={ACTIVITY_ICON[type] || "activity"} size={13} />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                          <span className="truncate text-[0.8125rem] font-semibold text-ink" title={a.title || type}>{a.title || type.replace(/_/g, " ")}</span>
                          <time className="shrink-0 text-xs tabular-nums text-ink-faint" dateTime={a.created_at}>{fmtDate(a.created_at)}</time>
                        </div>
                        {a.body ? <p className="mt-0.5 line-clamp-3 whitespace-pre-line text-[0.8125rem] leading-5 text-ink-muted">{a.body}</p> : null}
                      </div>
                    </li>
                  );
                })}
              </ol>
            )}
          </Panel>
        </div>

        {/* ---------------- Side column ---------------- */}
        <aside className="min-w-0 space-y-4" aria-label="Lead facts">
          <Panel title="Contact">
            <KeyValue columns={1} items={[
              { label: "Phone", value: phone ? <span className="flex items-center gap-1"><a className="link tabular-nums" href={`tel:${phone}`}>{phone}</a><CopyButton value={phone} /></span> : "—" },
              { label: "Email", value: email ? <span className="flex min-w-0 items-center gap-1"><a className="link truncate" href={`mailto:${email}`} title={email}>{email}</a><CopyButton value={email} /></span> : "—" },
              { label: "Owner", value: l.owner_name || l.decision_maker_name || "—" },
              { label: "Website", value: l.website ? <a className="link" href={l.website} target="_blank" rel="noreferrer" title={l.website}>{String(l.website).replace(/^https?:\/\//, "")}</a> : "—" },
              { label: "Location", value: <span title={location}>{location || "—"}</span> },
            ]} />
          </Panel>

          <Panel title="Quality and verification">
            <div className="space-y-4">
              {typeof score === "number" && <ScoreBar score={score} />}
              <QualityChips tier={l.tier} score={l.score ?? l.total_score} intent={l.intent_score} sources={l.sources_hit} />
              <div className="flex flex-wrap gap-1.5">
                {l.email_valid != null ? <Badge tone={l.email_valid ? "success" : "warn"}>{l.email_valid ? "Email verified" : "Email unverified"}</Badge> : null}
                {l.enrichment_ok != null ? <Badge tone={l.enrichment_ok ? "success" : "warn"}>{l.enrichment_ok ? "Enriched" : "Enrichment partial"}</Badge> : null}
                {l.has_booking ? <Badge tone="neutral">Booking{l.booking_platform ? `: ${l.booking_platform}` : ""}</Badge> : null}
                {l.business_status ? <StatusBadge status={l.business_status} /> : null}
              </div>
              {(l.review_snippets || l.reviews_sample) && (
                <div className="rounded-lg border border-line bg-surface-3/50 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <div className="label">Reviews signal</div>
                    <span className="text-xs tabular-nums text-ink-muted">
                      Rating {l.google_rating ?? l.rating ?? "—"} · {l.review_count ?? l.reviews ?? "—"} reviews
                    </span>
                  </div>
                  <ul className="mt-2 space-y-1.5 text-xs leading-5 text-ink-muted">
                    {reviews.slice(0, 5).map((r: any, i: number) => (
                      <li key={i}>“{typeof r === "string" ? r : r.text || r.snippet || JSON.stringify(r)}”</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </Panel>

          <Panel title="CRM stage" description="Move the lead when the conversation changes.">
            {stages.length ? (
              <div className="flex flex-wrap gap-1.5" role="group" aria-label="Lead stage">
                {stages.map((s: any) => {
                  const current = l.stage_id === s.id;
                  return (
                    <button key={s.id} disabled={busy} onClick={() => moveStage(s.id)} aria-pressed={current} className={`${current ? "btn-primary" : "btn-secondary"} btn-sm`}>
                      {s.name || s.slug}
                    </button>
                  );
                })}
              </div>
            ) : (
              <p className="text-[0.8125rem] text-ink-faint">No pipeline stages configured.</p>
            )}
          </Panel>

          <Panel title="Tasks" bodyClassName="p-0">
            {tasks.length ? (
              <ul className="divide-y divide-line">
                {tasks.slice(0, 20).map((t: any) => (
                  <li key={t.id} className="flex items-start justify-between gap-3 px-5 py-2.5">
                    <div className="min-w-0">
                      <div className={`truncate text-[0.8125rem] font-medium ${t.completed_at ? "text-ink-faint line-through" : "text-ink"}`} title={t.title}>{t.title}</div>
                      {t.due_at ? <div className="text-xs tabular-nums text-ink-faint">Due {fmtDate(t.due_at)}</div> : null}
                    </div>
                    <StatusBadge status={t.completed_at ? "completed" : t.status || "open"} />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="px-5 py-4 text-[0.8125rem] text-ink-faint">No open tasks.</p>
            )}
            <div className="flex gap-2 border-t border-line px-5 py-3">
              <input className="input h-9 flex-1" value={taskTitle} onChange={(e) => setTaskTitle(e.target.value)} placeholder="Follow-up task" aria-label="Follow-up task title" />
              <button aria-label="Add" disabled={busy} onClick={addTask} className="btn-secondary btn-sm">Add</button>
            </div>
          </Panel>

          <Panel title="Report bad lead" description="Wrong numbers and closed businesses are credited back after review.">
            <div className="flex gap-2">
              <select className="select h-9 flex-1" value={badReason} onChange={(e) => setBadReason(e.target.value)} aria-label="Bad lead reason">
                <option value="wrong_number">Wrong number</option>
                <option value="email_bounce">Email bounce</option>
                <option value="closed">Closed</option>
                <option value="duplicate">Duplicate</option>
                <option value="other">Other</option>
              </select>
              <button aria-label="Report" disabled={busy} onClick={reportBad} className="btn-danger btn-sm">Report</button>
            </div>
          </Panel>
        </aside>
      </div>
    </div>
  );
}

/** Lead summary header: PageHeader's structure with an inline badge row under the name. */
function PageHeaderLocal({ title, location, badges, actions }: { title: string; location?: string; badges?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div className="min-w-0">
        <div className="eyebrow text-brand-600 dark:text-brand-300">Lead</div>
        <h1 className="page-title mt-1.5 truncate" title={title}>{title}</h1>
        {location ? <p className="page-sub flex items-center gap-1.5"><Icon name="territory" size={14} className="shrink-0 text-ink-faint" /><span className="truncate" title={location}>{location}</span></p> : null}
        {badges ? <div className="mt-3 flex flex-wrap items-center gap-1.5">{badges}</div> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}
