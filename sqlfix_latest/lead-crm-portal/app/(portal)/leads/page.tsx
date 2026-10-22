"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { crm, LeadCard } from "@/lib/api";
import { getTenantId, getTenantUserId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { TierBadge, StagePill } from "@/components/leads/TierBadge";
import { QualityChips } from "@/components/leads/QualityChips";
import { CopyButton } from "@/components/ui/CopyButton";
import { SavedViews } from "@/components/leads/SavedViews";
import { BulkActionBar } from "@/components/BulkActionBar";
import { useChangedIds } from "@/lib/live";
import { VariableVirtualList } from "@/lib/VariableVirtualList";
import { ErrorState, EmptyState } from "@/components/ui/States";
import { EvidenceNote, FreshnessNote, UnavailableNote } from "@/components/ui/SurfaceMeta";


/** Rows come from two shapes (view vs search-run); both carry one of these. */
const leadId = (l: any): string => l.assignment_id || l.id;

function LeadRow({lead, selected, changed, toggle}: {lead:any; selected:Set<string>; changed:boolean; toggle:(id:string)=>void}) {
  const id = lead.assignment_id || lead.id;
  const phone = lead.phone_e164 || lead.phone;
  const email = lead.email;
  return <div className={`card-pad transition hover:shadow-lift [content-visibility:auto] [contain-intrinsic-size:220px] ${changed ? "row-flash" : ""}`}>
    <div className="flex flex-wrap items-start gap-4">
      <input type="checkbox" className="mt-1.5 h-4 w-4 rounded border-line" aria-label={`Select ${lead.name || lead.company_name || "lead"}`} checked={selected.has(id)} onChange={() => toggle(id)} />
      <div className="min-w-0 flex-1 space-y-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <Link href={`/leads/${id}`} className="text-lg font-semibold text-ink hover:text-brand-600 dark:hover:text-brand-300">{lead.name || lead.company_name || "Unnamed business"}</Link>
          <TierBadge tier={lead.tier} />
          {lead.stage_name && <StagePill stage={lead.stage_name} />}
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-ink-muted">
          {lead.city && <span>{lead.city}</span>}
          {phone && <span className="inline-flex items-center gap-1"><a className="font-medium text-signal-700 hover:underline dark:text-signal-300" href={`tel:${phone}`}>{phone}</a><CopyButton value={phone} /></span>}
          {email && <span className="inline-flex items-center gap-1"><a className="font-medium text-signal-700 hover:underline dark:text-signal-300" href={`mailto:${email}`}>{email}</a><CopyButton value={email} /></span>}
          {lead.website && <a href={lead.website} target="_blank" rel="noreferrer" className="hover:text-ink">Website ↗</a>}
        </div>
        <QualityChips tier={lead.tier} score={lead.score ?? lead.total_score} intent={lead.intent_score} opportunity={lead.opportunity_score_overall ?? lead.opportunity_score} fit={lead.fit_score} contactability={lead.contactability_score} sources={lead.sources_hit || lead.source} pitch={lead.pitch_line} talkingPoints={lead.talking_points} />
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <EvidenceNote count={Array.isArray(lead.opportunity_evidence) ? lead.opportunity_evidence.length : undefined} label="opportunity evidence" />
          {lead.source || lead.sources_hit ? <span className="text-[11px] text-ink-faint">Provider: {Array.isArray(lead.sources_hit) ? lead.sources_hit.slice(0, 2).join(", ") : lead.source}</span> : <UnavailableNote label="Provider" />}
          {lead.updated_at || lead.delivered_at || lead.created_at ? <FreshnessNote detail={new Date(lead.updated_at || lead.delivered_at || lead.created_at).toLocaleDateString()} /> : <FreshnessNote label="Freshness unavailable" />}
        </div>
        {Array.isArray(lead.opportunity_evidence) && lead.opportunity_evidence.length > 0 && <div className="flex flex-wrap gap-1.5 text-[11px] text-ink-faint">{lead.opportunity_evidence.slice(0, 3).map((e: string) => <span key={e} className="rounded-md bg-surface-3/70 px-2 py-1">{e}</span>)}</div>}
      </div>
      <div className="flex shrink-0 flex-col gap-2">
        {phone && <a href={`tel:${phone}`} className="btn-success text-center">Call</a>}
        <Link href={`/leads/${id}`} className="btn-secondary text-center">Open</Link>
      </div>
    </div>
  </div>;
}

export default function LeadsPage() {
  const [leads, setLeads] = useState<LeadCard[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<unknown>(null);
  const loadedOnceRef = useRef(false);
  const [search, setSearch] = useState("");
  const [tier, setTier] = useState("");
  const [stage, setStage] = useState("");
  const [sort, setSort] = useState("score_desc");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [seqOpen, setSeqOpen] = useState(false);
  const [sequences, setSequences] = useState<any[]>([]);
  const [seqPick, setSeqPick] = useState("");
  const [seqMsg, setSeqMsg] = useState("");
  const [seqBusy, setSeqBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkSubj, setBulkSubj] = useState("Quick idea for {{company}}");
  const [bulkBody, setBulkBody] = useState(
    "<p>Hi {{owner}},</p><p>I had a quick idea for {{company}} in {{city}}.</p><p>{{pitch}}</p><p>Open to a short call this week?</p>"
  );
  const [bulkBusy, setBulkBusy] = useState(false);
  const [tagBusy, setTagBusy] = useState(false);
  const changedLeadIds = useChangedIds(leads, (l: any) => String(leadId(l)));

  const load = useCallback(() => {
    if (!loadedOnceRef.current) setLoading(true);
    setLoadError(null);
    crm
      .leads(getTenantId(), {
        search: search || undefined,
        tier: tier || undefined,
        stage: stage || undefined,
        limit: 50,
      })
      .then((r) => {
        setLeads(r.leads || []);
        setTotal(r.total || 0);
      })
      .catch((e) => { setLoadError(e); setMsg(""); })
      .finally(() => { loadedOnceRef.current = true; setLoading(false); });
  }, [search, tier, stage]);

  useEffect(() => {
    const timer = window.setTimeout(load, search ? 220 : 0);
    return () => window.clearTimeout(timer);
  }, [load, search, tier, stage]);


  useCrmChange(load, { tables: ["lead_assignments", "leads_global", "lead_crm_cards", "lead_state_history", "lead_score_snapshots", "lead_quality_gates", "lead_verification_observations", "lead_outcome_events", "activities", "lead_activities"] });

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  /**
   * Bulk tag — there is no bulk endpoint, so this fans out PATCH
   * /leads/{assignment} (crm.updateLead), merging into each lead's existing
   * tags rather than replacing them.
   */
  async function addTagToSelected() {
    const tag = window.prompt("Tag to add to the selected leads")?.trim();
    if (!tag) return;
    setTagBusy(true);
    const targets = leads.filter((l) => selected.has(leadId(l)));
    const results = await Promise.allSettled(
      targets.map((l) =>
        crm.updateLead(getTenantId(), leadId(l), {
          tags: Array.from(new Set([...(l.tags || []), tag])),
        })
      )
    );
    const failed = results.filter((r) => r.status === "rejected").length;
    setMsg(`Tagged ${results.length - failed} lead(s) "${tag}"${failed ? `, ${failed} failed` : ""}`);
    setTagBusy(false);
    load();
  }

  async function sendSelectedEmail() {
    if (selected.size === 0) {
      setMsg("Select at least one lead, or use Send all");
      return;
    }
    setBulkBusy(true);
    try {
      const r: any = await crm.sendEmailBulk(getTenantId(), {
        subject: bulkSubj,
        body_html: bulkBody,
        assignment_ids: Array.from(selected),
      });
      setMsg(`Email: ${r.sent || 0} sent, ${r.failed || 0} failed, ${r.skipped_no_email || 0} no email`);
      setBulkOpen(false);
      setSelected(new Set());
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBulkBusy(false);
    }
  }

  async function sendAllEmail() {
    if (!confirm("Send this email to all leads matching current filters (with email, max 100)?")) return;
    setBulkBusy(true);
    try {
      const r: any = await crm.sendEmailBulk(getTenantId(), {
        subject: bulkSubj,
        body_html: bulkBody,
        tier: tier || undefined,
        stage: stage || undefined,
        limit: 100,
      });
      setMsg(`Send all: ${r.sent || 0} sent, ${r.failed || 0} failed, ${r.skipped_no_email || 0} skipped`);
      setBulkOpen(false);
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBulkBusy(false);
    }
  }

  async function handleExport() {
    try {
      const data = await crm.exportCsv(getTenantId(), {
        tier: tier || undefined,
        stage: stage || undefined,
      });
      const headers = Object.keys(data.rows[0] || {});
      const csv = [
        headers.join(","),
        ...data.rows.map((row: any) =>
          headers.map((h) => JSON.stringify(row[h] ?? "")).join(",")
        ),
      ].join("\n");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
      a.download = data.filename || "leads.csv";
      a.click();
      setMsg("CSV downloaded");
    } catch (e: any) {
      setMsg(e.message);
    }
  }


  const allSelected = leads.length > 0 && leads.every((l) => selected.has(leadId(l)));

  const sortedLeads = [...leads].sort((a: any, b: any) => {
    const tierRank = (t: string) => ({ A: 0, B: 1, C: 2 }[String(t || "").toUpperCase()] ?? 9);
    const score = (x: any) => Number(x.total_score ?? x.score ?? 0);
    const name = (x: any) => String(x.company_name || "").toLowerCase();
    const city = (x: any) => String(x.city || "").toLowerCase();
    const ts = (x: any) => new Date(x.delivered_at || x.created_at || 0).getTime();
    switch (sort) {
      case "score_asc":
        return score(a) - score(b);
      case "tier_asc":
        return tierRank(a.tier) - tierRank(b.tier) || score(b) - score(a);
      case "tier_desc":
        return tierRank(b.tier) - tierRank(a.tier) || score(b) - score(a);
      case "name_asc":
        return name(a).localeCompare(name(b));
      case "name_desc":
        return name(b).localeCompare(name(a));
      case "city_asc":
        return city(a).localeCompare(city(b));
      case "newest":
        return ts(b) - ts(a);
      case "score_desc":
      default:
        return score(b) - score(a);
    }
  });

  return (
    <div className="page">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="page-title">Leads</h1>
          <p className="page-sub">{total} quality-gated prospects · prioritize the next best conversation, not a static list.</p>
          <div className="mt-2 flex flex-wrap items-center gap-3"><FreshnessNote label="Live CRM view" detail={`${leads.length} loaded`} /><span className="text-[11px] text-ink-faint">Evidence and provider fields are shown only when returned by the API.</span></div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button aria-label="Action" onClick={() => setBulkOpen(true)} className="btn-primary">
            Email…
          </button>
          <button aria-label="Action" onClick={() => setSeqOpen(true)} className="btn-secondary">
            Enroll in sequence…
          </button>
          <button aria-label="Action" onClick={handleExport} className="btn-secondary">
            Export CSV
          </button>
          <button aria-label="Action" onClick={load} className="btn-primary">
            Refresh
          </button>
        </div>
      </header>

      {msg && (
        <div className="rounded-xl border border-line bg-surface-2 px-4 py-2.5 text-sm text-ink-muted">
          {msg}
        </div>
      )}

      {/* Filters */}
      <SavedViews
        current={{ search, tier, stage, sort }}
        onApply={(f) => {
          setSearch(f.search ?? "");
          setTier(f.tier ?? "");
          setStage(f.stage ?? "");
          setSort(f.sort ?? "score_desc");
        }}
      />
      <div className="card-pad flex flex-wrap gap-3">
        <input
          className="input min-w-[200px] flex-1"
          type="search"
          placeholder="Search company, city, phone…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select className="input max-w-[140px]" value={tier} onChange={(e) => setTier(e.target.value)}>
          <option value="">All tiers</option>
          <option value="A">Tier A only</option>
          <option value="B">Tier B</option>
        </select>
        <select className="input max-w-[160px]" value={stage} onChange={(e) => setStage(e.target.value)}>
          <option value="">All stages</option>
          <option value="new">New</option>
          <option value="contacted">Contacted</option>
          <option value="qualified">Qualified</option>
          <option value="won">Won</option>
          <option value="lost">Lost</option>
        </select>
        <select className="input max-w-[180px]" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="score_desc">Score: high → low</option>
          <option value="score_asc">Score: low → high</option>
          <option value="tier_asc">Tier: A → C</option>
          <option value="tier_desc">Tier: C → A</option>
          <option value="name_asc">Name: A → Z</option>
          <option value="name_desc">Name: Z → A</option>
          <option value="city_asc">City: A → Z</option>
          <option value="newest">Newest first</option>
        </select>
        <label className="flex items-center gap-2 text-sm text-ink-muted">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-line"
            aria-label="Select all leads shown"
            checked={allSelected}
            onChange={(e) =>
              setSelected(e.target.checked ? new Set(leads.map(leadId)) : new Set())
            }
          />
          Select all
        </label>
      </div>

      <div className="rounded-xl border border-brand-200 bg-brand-50 px-4 py-3 text-sm text-brand-900 dark:border-brand-500/20 dark:bg-brand-500/10 dark:text-brand-200">
        <strong>Tip:</strong> Start with Tier A → use the pitch line → call → update stage. Report bad numbers for credit.
      </div>

      {loading ? (
        <div className="py-20 text-center text-ink-faint" aria-busy="true">Loading leads…</div>
      ) : loadError ? (
        <ErrorState error={loadError} onRetry={load} />
      ) : leads.length === 0 ? (
        <div className="card"><EmptyState title="No leads match these filters" hint="Clear a filter or wait for the next delivery batch. LeadX will keep this view current through the CRM stream." /></div>
      ) : (
        <>
        {sortedLeads.length > 100 ? (
          <VariableVirtualList items={sortedLeads} estimate={220} height={760} getKey={(lead:any)=>leadId(lead)} render={(lead:any) => <LeadRow lead={lead} selected={selected} changed={changedLeadIds.has(String(leadId(lead)))} toggle={toggle} />} />
        ) : (
          <div className="space-y-3">
            {sortedLeads.map((lead:any) => <LeadRow key={leadId(lead)} lead={lead} selected={selected} changed={changedLeadIds.has(String(leadId(lead)))} toggle={toggle} />)}
          </div>
        )}
        </>
      )}

      {bulkOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="card-pad max-h-[90vh] w-full max-w-lg space-y-3 overflow-y-auto shadow-lift">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold text-ink">Send email</h2>
              <button aria-label="Action" className="btn-ghost" onClick={() => setBulkOpen(false)}>Close</button>
            </div>
            <p className="text-xs text-ink-faint">
              Merge tags: {"{{name}}"} {"{{company}}"} {"{{owner}}"} {"{{city}}"} {"{{pitch}}"} {"{{tier}}"}
            </p>
            <input className="input" value={bulkSubj} onChange={(e) => setBulkSubj(e.target.value)} placeholder="Subject" />
            <textarea className="input min-h-[160px] font-mono text-xs" value={bulkBody} onChange={(e) => setBulkBody(e.target.value)} />
            <div className="flex flex-wrap gap-2">
              <button aria-label="Action" disabled={bulkBusy || selected.size === 0} onClick={sendSelectedEmail} className="btn-primary">
                Email selected ({selected.size})
              </button>
              <button aria-label="Action" disabled={bulkBusy} onClick={sendAllEmail} className="btn-secondary">
                Send all (filtered)
              </button>
            </div>
            <p className="text-xs text-ink-faint">Uses Resend or SMTP. Max 100 per batch. Leads without email are skipped.</p>
          </div>
        </div>
      )}

      {seqOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="card-pad w-full max-w-md space-y-3 shadow-lift">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold text-ink">Enroll in sequence</h2>
              <button aria-label="Action" className="btn-ghost" onClick={() => setSeqOpen(false)}>Close</button>
            </div>
            {selected.size === 0 && (
              <p className="text-sm text-rust-500 dark:text-rust-300">Select leads first (checkboxes in the table).</p>
            )}
            <select
              className="input"
              value={seqPick}
              onChange={(e) => setSeqPick(e.target.value)}
              onFocus={() => { if (!sequences.length) crm.sequences(getTenantId()).then((d: any) => setSequences(d.sequences || [])).catch(() => {}); }}
            >
              <option value="">Choose a sequence…</option>
              {sequences.map((s: any) => (
                <option key={s.id} value={s.id}>{s.name} ({s.steps?.length || 0} steps)</option>
              ))}
            </select>
            <button aria-label="Action"
              disabled={seqBusy || !seqPick || selected.size === 0}
              onClick={async () => {
                setSeqBusy(true);
                setSeqMsg("");
                try {
                  const r: any = await crm.enrollSequence(getTenantId(), seqPick, Array.from(selected));
                  setSeqMsg(`Enrolled ${r.enrolled}, skipped ${r.skipped}`);
                } catch (e: any) {
                  setSeqMsg(e.message);
                } finally {
                  setSeqBusy(false);
                }
              }}
              className="btn-primary w-full"
            >
              Enroll {selected.size} lead{selected.size === 1 ? "" : "s"}
            </button>
            {seqMsg && <p className="text-xs text-ink-muted">{seqMsg}</p>}
            <p className="text-xs text-ink-faint">
              First step sends after its configured delay. Stops automatically if the lead unsubscribes or replies.
            </p>
          </div>
        </div>
      )}

      <BulkActionBar
        count={selected.size}
        noun={`lead${selected.size === 1 ? "" : "s"} selected`}
        onClear={() => setSelected(new Set())}
        actions={[
          { label: "Email…", onClick: () => setBulkOpen(true) },
          { label: "Enroll in sequence…", onClick: () => setSeqOpen(true) },
          { label: "Add tag…", onClick: addTagToSelected, disabled: tagBusy },
        ]}
      />
    </div>
  );
}
