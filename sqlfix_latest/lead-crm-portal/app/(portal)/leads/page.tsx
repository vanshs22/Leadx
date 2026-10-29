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
import { ErrorState, EmptyState, LoadingRows } from "@/components/ui/States";
import { EvidenceNote, FreshnessNote } from "@/components/ui/SurfaceMeta";
import { PageHeader, FilterBar, Panel, Tabs, Drawer, Notice, KeyValue } from "@/components/ui/Primitives";
import { Icon } from "@/components/ui/Icon";

/** Rows come from two shapes (view vs search-run); both carry one of these. */
const leadId = (l: any): string => l.assignment_id || l.id;

/* Fixed column widths so the virtualised path (one table per row) aligns with the header. */
function LeadCols() {
  return (
    <colgroup>
      <col className="w-11" />
      <col />
      <col className="w-[96px]" />
      <col className="w-[120px]" />
      <col className="w-[230px]" />
      <col className="w-[72px]" />
      <col className="w-[96px]" />
      <col className="w-[104px]" />
      <col className="w-[150px]" />
    </colgroup>
  );
}

function LeadHead({ allSelected, onToggleAll }: { allSelected: boolean; onToggleAll: (checked: boolean) => void }) {
  return (
    <thead>
      <tr className="[&>th]:bg-surface-3">
        <th scope="col" className="!pr-0"><input type="checkbox" className="h-4 w-4 rounded border-line-strong accent-brand-700" aria-label="Select all leads shown" checked={allSelected} onChange={(e) => onToggleAll(e.target.checked)} /></th>
        <th scope="col">Business</th>
        <th scope="col">Tier</th>
        <th scope="col">Stage</th>
        <th scope="col">Contact</th>
        <th scope="col" className="!text-right">Score</th>
        <th scope="col" className="!text-right">Opportunity</th>
        <th scope="col" className="!text-right">Updated</th>
        <th scope="col" className="!text-right"><span className="sr-only">Actions</span></th>
      </tr>
    </thead>
  );
}

function leadUpdated(lead: any): string | null {
  const raw = lead.updated_at || lead.delivered_at || lead.created_at;
  return raw ? new Date(raw).toLocaleDateString() : null;
}

function LeadRow({lead, selected, changed, toggle, onPreview}: {lead:any; selected:Set<string>; changed:boolean; toggle:(id:string)=>void; onPreview:(lead:any)=>void}) {
  const id = lead.assignment_id || lead.id;
  const phone = lead.phone_e164 || lead.phone;
  const email = lead.email;
  const name = lead.name || lead.company_name || "Unnamed business";
  const score = lead.score ?? lead.total_score;
  const opportunity = lead.opportunity_score_overall ?? lead.opportunity_score;
  const evidence: string[] = Array.isArray(lead.opportunity_evidence) ? lead.opportunity_evidence : [];
  const why = evidence[0] || lead.pitch_line || lead.talking_points;
  const provider = Array.isArray(lead.sources_hit) ? lead.sources_hit.slice(0, 2).join(", ") : lead.source;
  const updated = leadUpdated(lead);
  const isSelected = selected.has(id);
  return (
    <tr className={`${isSelected ? "bg-brand-50/70 dark:bg-brand-500/10" : ""} ${changed ? "row-flash" : ""}`} data-selected={isSelected || undefined}>
      <td className="!pr-0 !py-2.5"><input type="checkbox" className="h-4 w-4 rounded border-line-strong accent-brand-700" aria-label={`Select ${name}`} checked={isSelected} onChange={() => toggle(id)} /></td>
      <td className="!py-2.5">
        <div className="min-w-0">
          <Link href={`/leads/${id}`} title={name} className="block truncate font-semibold text-ink hover:text-brand-700 dark:hover:text-brand-300">{name}</Link>
          <div className="mt-0.5 flex min-w-0 items-center gap-1.5 text-xs text-ink-faint">
            {lead.city ? <span className="truncate" title={lead.city}>{lead.city}</span> : null}
            {lead.city && provider ? <span aria-hidden="true">·</span> : null}
            {provider ? <span className="truncate" title={`Provider: ${provider}`}>{provider}</span> : null}
            {!lead.city && !provider ? <span>Location and provider unavailable</span> : null}
          </div>
          {why ? <p className="mt-1 truncate text-xs text-ink-muted" title={why}><span className="font-medium text-signal-700 dark:text-signal-300">Why: </span>{why}</p> : null}
        </div>
      </td>
      <td className="!py-2.5"><TierBadge tier={lead.tier} /></td>
      <td className="!py-2.5">{lead.stage_name ? <StagePill stage={lead.stage_name} /> : <span className="text-xs text-ink-faint">—</span>}</td>
      <td className="!py-2.5">
        <div className="min-w-0 space-y-0.5 text-xs">
          {phone ? <div className="flex min-w-0 items-center gap-1"><a className="truncate font-medium tabular-nums text-brand-700 hover:underline dark:text-brand-300" title={phone} href={`tel:${phone}`}>{phone}</a><CopyButton value={phone} /></div> : null}
          {email ? <div className="flex min-w-0 items-center gap-1"><a className="truncate text-ink-muted hover:text-ink hover:underline" title={email} href={`mailto:${email}`}>{email}</a><CopyButton value={email} /></div> : null}
          {!phone && !email ? <span className="text-ink-faint">No contact on file</span> : null}
        </div>
      </td>
      <td className={`!py-2.5 text-right font-semibold tabular-nums ${Number(score) >= 85 ? "text-signal-700 dark:text-signal-300" : "text-ink"}`}>{typeof score === "number" ? score : "—"}</td>
      <td className="!py-2.5 text-right tabular-nums text-ink-muted">{typeof opportunity === "number" ? opportunity : "—"}</td>
      <td className="!py-2.5 text-right text-xs tabular-nums text-ink-faint" title={updated ? `Updated ${updated}` : "Freshness unavailable"}>{updated || "—"}</td>
      <td className="!py-2.5">
        <div className="flex items-center justify-end gap-1">
          {phone ? <a href={`tel:${phone}`} className="btn-ghost btn-sm !px-2" aria-label={`Call ${name}`} title="Call"><Icon name="phone" size={14} /></a> : null}
          <button type="button" className="btn-ghost btn-sm !px-2" aria-label={`Preview ${name}`} title="Preview" onClick={() => onPreview(lead)}><Icon name="info" size={14} /></button>
          <Link href={`/leads/${id}`} className="btn-secondary btn-sm">Open</Link>
        </div>
      </td>
    </tr>
  );
}

function LeadPreview({ lead }: { lead: any }) {
  const id = lead.assignment_id || lead.id;
  const phone = lead.phone_e164 || lead.phone;
  const evidence: string[] = Array.isArray(lead.opportunity_evidence) ? lead.opportunity_evidence : [];
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <TierBadge tier={lead.tier} />
        {lead.stage_name ? <StagePill stage={lead.stage_name} /> : null}
      </div>
      <KeyValue items={[
        { label: "City", value: lead.city || "—" },
        { label: "Phone", value: phone ? <a className="link tabular-nums" href={`tel:${phone}`}>{phone}</a> : "—" },
        { label: "Email", value: lead.email ? <a className="link" href={`mailto:${lead.email}`} title={lead.email}>{lead.email}</a> : "—" },
        { label: "Website", value: lead.website ? <a className="link" href={lead.website} target="_blank" rel="noreferrer" title={lead.website}>{lead.website.replace(/^https?:\/\//, "")}</a> : "—" },
        { label: "Updated", value: <span className="tabular-nums">{leadUpdated(lead) || "—"}</span> },
      ]} />
      <div>
        <div className="label mb-2">Quality</div>
        <QualityChips tier={lead.tier} score={lead.score ?? lead.total_score} intent={lead.intent_score} opportunity={lead.opportunity_score_overall ?? lead.opportunity_score} fit={lead.fit_score} contactability={lead.contactability_score} sources={lead.sources_hit || lead.source} pitch={lead.pitch_line} talkingPoints={lead.talking_points} />
      </div>
      <div>
        <div className="flex items-center justify-between gap-2"><div className="label">Why this lead</div><EvidenceNote count={Array.isArray(lead.opportunity_evidence) ? evidence.length : undefined} label="opportunity evidence" /></div>
        {evidence.length ? (
          <ul className="mt-2 space-y-1.5">
            {evidence.slice(0, 6).map((e) => <li key={e} className="rounded-md border border-line bg-surface-3/60 px-3 py-2 text-[0.8125rem] text-ink-muted">{e}</li>)}
          </ul>
        ) : <p className="mt-2 text-[0.8125rem] text-ink-faint">No opportunity evidence returned for this lead.</p>}
      </div>
      <Link href={`/leads/${id}`} className="btn-primary w-full">Open full record</Link>
    </div>
  );
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
  const [preview, setPreview] = useState<any | null>(null);
  const closePreview = useCallback(() => setPreview(null), []);
  const closeBulk = useCallback(() => setBulkOpen(false), []);
  const closeSeq = useCallback(() => setSeqOpen(false), []);
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


  const tierCounts = leads.reduce((acc: Record<string, number>, l: any) => {
    const t = String(l.tier || "").toUpperCase();
    acc[t] = (acc[t] || 0) + 1;
    return acc;
  }, {});

  const toggleAll = (checked: boolean) => setSelected(checked ? new Set(leads.map(leadId)) : new Set());

  return (
    <div className="page">
      <PageHeader
        eyebrow="CRM"
        title="Leads"
        description={<><span className="tabular-nums">{total.toLocaleString()}</span> quality-gated prospects. Prioritize the next best conversation, not a static list.</>}
        meta={<><FreshnessNote label="Live CRM view" detail={`${leads.length} loaded`} /><span>Evidence and provider fields are shown only when returned by the API.</span></>}
        actions={<>
          <button aria-label="Refresh leads" onClick={load} className="btn-ghost"><Icon name="refresh" size={14} />Refresh</button>
          <button aria-label="Export leads as CSV" onClick={handleExport} className="btn-secondary"><Icon name="download" size={14} />Export CSV</button>
          <button aria-label="Enroll selected leads" onClick={() => setSeqOpen(true)} className="btn-secondary"><Icon name="sequence" size={14} />Enroll in sequence…</button>
          <button aria-label="Email selected leads" onClick={() => setBulkOpen(true)} className="btn-primary"><Icon name="mail" size={14} />Email…</button>
        </>}
      />

      {msg && <Notice tone="neutral" onDismiss={() => setMsg("")}>{msg}</Notice>}

      <div className="space-y-3">
        <FilterBar onClear={() => { setSearch(""); setTier(""); setStage(""); setSort("score_desc"); }}>
          <div className="relative min-w-[220px] flex-1">
            <Icon name="search" size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint" />
            <input className="input h-9 pl-8" type="search" placeholder="Search company, city, phone…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search leads" />
          </div>
          <Tabs
            label="Filter leads by tier"
            value={(tier || "all") as "all" | "A" | "B"}
            onChange={(v) => setTier(v === "all" ? "" : v)}
            items={[
              { value: "all", label: "All tiers" },
              { value: "A", label: "Tier A", count: tierCounts.A },
              { value: "B", label: "Tier B", count: tierCounts.B },
            ]}
          />
          <select className="select h-9 w-auto min-w-[140px]" value={stage} onChange={(e) => setStage(e.target.value)} aria-label="Filter leads by stage"><option value="">All stages</option><option value="new">New</option><option value="contacted">Contacted</option><option value="qualified">Qualified</option><option value="won">Won</option><option value="lost">Lost</option></select>
          <select className="select h-9 w-auto min-w-[170px]" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort leads"><option value="score_desc">Score: high → low</option><option value="score_asc">Score: low → high</option><option value="tier_asc">Tier: A → C</option><option value="tier_desc">Tier: C → A</option><option value="name_asc">Name: A → Z</option><option value="name_desc">Name: Z → A</option><option value="city_asc">City: A → Z</option><option value="newest">Newest first</option></select>
        </FilterBar>
        <SavedViews
          current={{ search, tier, stage, sort }}
          onApply={(f) => { setSearch(f.search ?? ""); setTier(f.tier ?? ""); setStage(f.stage ?? ""); setSort(f.sort ?? "score_desc"); }}
        />
      </div>

      <Panel
        title="Lead list"
        description="Start with Tier A, use the pitch line, call, then update the stage. Report bad numbers for credit."
        actions={<span className="text-xs tabular-nums text-ink-faint" aria-live="polite">{selected.size ? `${selected.size} selected · ` : ""}{sortedLeads.length} of {total.toLocaleString()} shown</span>}
        bodyClassName="p-0"
      >
        {loading ? (
          <LoadingRows rows={8} cols={6} />
        ) : loadError ? (
          <div className="p-5"><ErrorState error={loadError} onRetry={load} /></div>
        ) : leads.length === 0 ? (
          <EmptyState title="No leads match these filters" hint="Clear a filter or wait for the next delivery batch. LeadX will keep this view current through the CRM stream." />
        ) : sortedLeads.length > 100 ? (
          <div className="overflow-x-auto">
            <table className="data-table min-w-[1080px] table-fixed">
              <caption className="sr-only">Leads</caption>
              <LeadCols />
              <LeadHead allSelected={allSelected} onToggleAll={toggleAll} />
            </table>
            <div className="min-w-[1080px]">
              <VariableVirtualList items={sortedLeads} estimate={72} height={760} getKey={(lead:any)=>leadId(lead)} render={(lead:any) => (
                <table className="data-table min-w-[1080px] table-fixed border-t border-line/70">
                  <LeadCols />
                  <tbody><LeadRow lead={lead} selected={selected} changed={changedLeadIds.has(String(leadId(lead)))} toggle={toggle} onPreview={setPreview} /></tbody>
                </table>
              )} />
            </div>
          </div>
        ) : (
          <div className="max-h-[calc(100vh-16rem)] min-h-[320px] overflow-auto">
            <table className="data-table min-w-[1080px] table-fixed">
              <caption className="sr-only">Leads</caption>
              <LeadCols />
              <LeadHead allSelected={allSelected} onToggleAll={toggleAll} />
              <tbody>
                {sortedLeads.map((lead:any) => <LeadRow key={leadId(lead)} lead={lead} selected={selected} changed={changedLeadIds.has(String(leadId(lead)))} toggle={toggle} onPreview={setPreview} />)}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Drawer open={!!preview} title={preview ? (preview.name || preview.company_name || "Unnamed business") : "Lead"} onClose={closePreview}>
        {preview ? <LeadPreview lead={preview} /> : null}
      </Drawer>

      <Drawer
        open={bulkOpen}
        title="Send email"
        onClose={closeBulk}
        footer={
          <div className="flex flex-wrap items-center justify-end gap-2">
            <button type="button" className="btn-ghost" onClick={() => setBulkOpen(false)}>Cancel</button>
            <button disabled={bulkBusy} onClick={sendAllEmail} className="btn-secondary">
              Send all (filtered)
            </button>
            <button disabled={bulkBusy || selected.size === 0} onClick={sendSelectedEmail} className="btn-primary">
              Email selected (<span className="tabular-nums">{selected.size}</span>)
            </button>
          </div>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="field-label" htmlFor="leads-bulk-subject">Subject</label>
            <input id="leads-bulk-subject" className="input" value={bulkSubj} onChange={(e) => setBulkSubj(e.target.value)} placeholder="Subject" />
          </div>
          <div>
            <label className="field-label" htmlFor="leads-bulk-body">Body (HTML)</label>
            <textarea id="leads-bulk-body" className="textarea min-h-[200px] font-mono text-xs" value={bulkBody} onChange={(e) => setBulkBody(e.target.value)} aria-describedby="leads-bulk-tags" />
            <p id="leads-bulk-tags" className="field-hint">
              Merge tags: <code className="font-mono">{"{{name}}"} {"{{company}}"} {"{{owner}}"} {"{{city}}"} {"{{pitch}}"} {"{{tier}}"}</code>
            </p>
          </div>
          <Notice tone="neutral">Uses Resend or SMTP. Max 100 per batch. Leads without email are skipped.</Notice>
        </div>
      </Drawer>

      <Drawer
        open={seqOpen}
        title="Enroll in sequence"
        onClose={closeSeq}
        footer={
          <div className="flex flex-wrap items-center justify-end gap-2">
            <button type="button" className="btn-ghost" onClick={() => setSeqOpen(false)}>Cancel</button>
            <button
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
              className="btn-primary"
            >
              Enroll {selected.size} lead{selected.size === 1 ? "" : "s"}
            </button>
          </div>
        }
      >
        <div className="space-y-4">
          {selected.size === 0 && (
            <Notice tone="warning">Select leads first (checkboxes in the table).</Notice>
          )}
          <div>
            <label className="field-label" htmlFor="leads-seq-pick">Sequence</label>
            <select
              id="leads-seq-pick"
              className="select"
              value={seqPick}
              onChange={(e) => setSeqPick(e.target.value)}
              onFocus={() => { if (!sequences.length) crm.sequences(getTenantId()).then((d: any) => setSequences(d.sequences || [])).catch(() => {}); }}
            >
              <option value="">Choose a sequence…</option>
              {sequences.map((s: any) => (
                <option key={s.id} value={s.id}>{s.name} ({s.steps?.length || 0} steps)</option>
              ))}
            </select>
            <p className="field-hint">
              First step sends after its configured delay. Stops automatically if the lead unsubscribes or replies.
            </p>
          </div>
          {seqMsg && <p className="text-[0.8125rem] text-ink-muted" role="status" aria-live="polite">{seqMsg}</p>}
        </div>
      </Drawer>

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
