"use client";

import { useLiveLoader } from "@/lib/live";
import { ErrorState, EmptyState, PollingDeadlineState } from "@/components/ui/States";
import { OperationStateCard, toOperationState } from "@/components/ui/OperationState";
import { useMemo, useRef, useState } from "react";
import { crm } from "@/lib/api";
import { useCrmChange } from "@/lib/crmRealtime";
import { getTenantId } from "@/lib/tenant";


export default function SearchPage() {
  const [vertical, setVertical] = useState("medspa");
  const [geo, setGeo] = useState("Hoboken, New Jersey");
  const [keywords, setKeywords] = useState("med spa, botox, aesthetics");
  const [offer, setOffer] = useState("lead_gen_service");
  const [limit, setLimit] = useState(15);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState("");
  const [runInfo, setRunInfo] = useState<any>(null);
  const [leads, setLeads] = useState<any[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [onlyEmail, setOnlyEmail] = useState(false);
  const [emailOpen, setEmailOpen] = useState(false);
  const [subj, setSubj] = useState("Proposal for {{company}} — exclusive local leads");
  const [body, setBody] = useState(
    "<p>Hi {{owner}},</p><p>We help <b>{{company}}</b> in {{city}} get exclusive verified local leads.</p><p>{{pitch}}</p><p>Sample pack this week?</p>"
  );
  const [sending, setSending] = useState(false);
  const [recipe, setRecipe] = useState("website_gap");
  const [quota, setQuota] = useState<any>(null);
  const [requests, setRequests] = useState<any[]>([]);
  const [requestError, setRequestError] = useState<unknown>(null);
  const [requestsLoading, setRequestsLoading] = useState(true);
  const [pollingExpired, setPollingExpired] = useState(false);
  const [pollingReset, setPollingReset] = useState(0);
  const requestsLoaded = useRef(false);
  const requestGeneration = useRef(0);
  const activeRequestId = useRef<string | null>(null);

  async function refreshRequests() {
    const generation = ++requestGeneration.current;
    if (!requestsLoaded.current) setRequestsLoading(true);
    try {
      const tid = getTenantId();
      const [r, q] = await Promise.all([crm.discoveryRequests(tid), crm.searchQuota(tid)]);
      if (generation !== requestGeneration.current) return;
      const rows = r.requests || [];
      const requestId = activeRequestId.current || rows[0]?.id || null;
      let status: any = null;
      let statusError: unknown = null;
      if (requestId) {
        try { status = await crm.discoveryRequestStatus(tid, requestId); }
        catch (error) { statusError = error; }
        if (generation !== requestGeneration.current) return;
      }
      setRequests(rows);
      setQuota(q);
      if (status) setRunInfo(status);
      setRequestError(statusError);
    } catch (e: unknown) {
      if (generation === requestGeneration.current) setRequestError(e);
    } finally {
      if (generation === requestGeneration.current) {
        requestsLoaded.current = true;
        setRequestsLoading(false);
      }
    }
  }
  const RECIPES: Record<string, { label: string; hint: string; keywords: string[] }> = {
    website_gap: { label: "Website / conversion gap", hint: "Businesses where a better site or conversion flow can create demand.", keywords: ["website", "local business"] },
    booking_gap: { label: "No-booking opportunity", hint: "Appointment-led businesses where booking/intake automation is valuable.", keywords: ["appointment", "booking"] },
    reputation: { label: "Reputation recovery", hint: "Businesses with enough review volume to support reputation work.", keywords: ["reviews", "rating"] },
    growth: { label: "Growth / hiring signal", hint: "Businesses showing expansion or staffing activity.", keywords: ["hiring", "growth"] },
    automation: { label: "Automation opportunity", hint: "Service businesses where calls, intake and follow-up can be automated.", keywords: ["service business", "appointments"] },
  };
  const activeRecipe = RECIPES[recipe] || RECIPES.website_gap;

  const refreshRequestState = () => {
    setPollingExpired(false);
    setPollingReset((value) => value + 1);
    void refreshRequests();
  };

  useLiveLoader(refreshRequests, 20_000, [pollingReset], {
    onDeadline: () => setPollingExpired(true),
  });
  useCrmChange(() => { void refreshRequests(); }, { tables: ["discovery_requests", "pipeline_runs", "jobs", "lead_assignments", "lead_quality_gates", "lead_verification_observations", "lead_score_snapshots", "quota_usage"] });

  const visible = useMemo(() => (onlyEmail ? leads.filter((l) => l.email) : leads), [leads, onlyEmail]);

  function toggle(id: string) {
    setSelected((prev) => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  }

  async function findLeads() {
    setLoading(true); setMsg(""); setRequestError(null); setSelected(new Set());
    try {
      const requested = Math.max(1, Number(limit) || 15);
      const r = await crm.createDiscoveryRequest(getTenantId(), {
        vertical: vertical.trim(),
        geo: geo.split(",").map((s) => s.trim()).filter(Boolean),
        keywords: Array.from(new Set([...keywords.split(",").map((s) => s.trim()).filter(Boolean), ...activeRecipe.keywords])),
        offer_category: offer.trim() || "lead_gen_service",
        requested_leads: requested,
        name: `${vertical.trim()} — ${geo.trim()}`,
        save_icp: true,
      });
      setMsg("Request submitted. It is now awaiting approval; you can leave this page and LeadX will keep the request safely queued.");
      activeRequestId.current = r.request.id;
      setRunInfo({ request: r.request, request_id: r.request.id, run_id: r.run_id || null, job_id: r.job_id || null, status: r.status });
      await refreshRequests();
    } catch (e: unknown) {
      setRequestError(e);
      setMsg("");
    }
    finally { setLoading(false); }
  }

  async function sendProposal() {
    if (!selected.size) return setMsg("Select leads first");
    setSending(true);
    try {
      const r: any = await crm.sendEmailBulk(getTenantId(), {
        subject: subj,
        body_html: body,
        assignment_ids: Array.from(selected),
      });
      setMsg(`Emails: ${r.sent || 0} sent · ${r.failed || 0} failed · ${r.skipped_no_email || 0} skipped`);
      setEmailOpen(false);
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setSending(false);
    }
  }

  const activeOperation = runInfo ? toOperationState(runInfo, { status: runInfo.status || runInfo.request?.status || "queued", stage: runInfo.request?.status || "awaiting approval", requestId: runInfo.request_id || runInfo.request?.id }) : null;

  return (
    <div className="page">
      <header>
        <h1 className="page-title">Search & outreach</h1>
        <p className="page-sub">Find businesses with a problem you can solve—not just a list of names.</p>
      </header>

      <div className="hero-panel space-y-4 p-5 md:p-6">
        <div className="mb-1 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
          {Object.entries(RECIPES).map(([key, r]) => (
            <button key={key} type="button" onClick={() => setRecipe(key)} className={`rounded-xl border p-3 text-left transition ${recipe === key ? "border-brand-400 bg-brand-50/70 shadow-sm dark:border-brand-400/60 dark:bg-brand-500/10" : "border-line bg-surface-2 hover:bg-surface-3"}`}>
              <div className="text-xs font-semibold text-ink">{r.label}</div>
              <div className="mt-1 text-[11px] leading-4 text-ink-faint">{r.hint}</div>
            </button>
          ))}
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-xs font-medium text-ink-faint">
            Who to find (vertical)
            <input className="input mt-1" value={vertical} onChange={(e) => setVertical(e.target.value)} />
          </label>
          <label className="block text-xs font-medium text-ink-faint">
            Where (geo, comma-separated)
            <input className="input mt-1" value={geo} onChange={(e) => setGeo(e.target.value)} />
          </label>
          <label className="block text-xs font-medium text-ink-faint sm:col-span-2">
            Keywords
            <input className="input mt-1" value={keywords} onChange={(e) => setKeywords(e.target.value)} />
          </label>
          <label className="block text-xs font-medium text-ink-faint">
            Your offer category
            <input className="input mt-1" value={offer} onChange={(e) => setOffer(e.target.value)} />
          </label>
          <label className="block text-xs font-medium text-ink-faint">
            Leads requested (up to monthly plan)
            <input className="input mt-1" type="number" min={1} max={quota?.limit || 500} value={limit} onChange={(e) => setLimit(Math.min(Number(e.target.value) || 1, quota?.limit || 500))} />
          </label>
        </div>
        <div className="flex items-center justify-between gap-3 rounded-xl border border-line bg-surface-3/60 px-3 py-2 text-xs text-ink-muted"><span><b className="text-ink">Recipe:</b> {activeRecipe.label}</span><span>{activeRecipe.keywords.length} discovery signals</span></div>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="text-xs text-ink-muted"><span className="font-semibold text-ink">Evidence-first discovery:</span> source, fit, contactability and opportunity signals are evaluated before delivery.</div>
          <button disabled={loading} onClick={findLeads} className="btn-primary px-6">
          {loading ? "Submitting…" : "Request opportunities"}
          </button>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <div className="card-pad"><div className="text-xs text-ink-faint">Monthly lead allowance</div><div className="mt-1 text-2xl font-semibold">{quota?.limit ?? "—"}</div></div>
        <div className="card-pad"><div className="text-xs text-ink-faint">Used / reserved</div><div className="mt-1 text-2xl font-semibold">{quota ? `${quota.used} / ${quota.reserved}` : "—"}</div></div>
        <div className="card-pad"><div className="text-xs text-ink-faint">Available</div><div className="mt-1 text-2xl font-semibold">{quota?.remaining ?? "—"}</div></div>
      </div>

      {requestError ? <ErrorState error={requestError} onRetry={refreshRequestState} /> : null}
      {pollingExpired ? <PollingDeadlineState onRefresh={refreshRequestState} /> : null}
      {activeOperation ? (
        <OperationStateCard operation={activeOperation} title="Latest discovery request" onRefresh={refreshRequestState} />
      ) : null}

      {requestsLoading ? (
        <div className="card-pad text-sm text-ink-faint" aria-busy="true">Loading discovery requests…</div>
      ) : null}
      {!requestsLoading && !requestError && requests.length === 0 ? (
        <div className="card-pad"><EmptyState title="No discovery requests yet" hint="Choose a recipe and request opportunities to start a tenant-scoped run." /></div>
      ) : null}
      {requests.length > 0 && <div className="card-pad space-y-2">
        <div className="font-semibold text-ink">Recent discovery requests</div>
        {requests.slice(0, 6).map((r: any) => {
          const operation = toOperationState(r, { status: r.status || "queued", stage: r.status || "queued" });
          return <div key={r.id} className="rounded-xl border border-line px-3 py-2 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2"><span className="font-medium text-ink">{r.vertical} · {(r.locations || []).join(", ") || "Any location"}</span><span className="pill">{r.status}</span></div>
            <div className="mt-1 text-xs text-ink-muted">Requested {r.requested_leads}{r.approved_leads ? ` · Approved ${r.approved_leads}` : ""} · {r.assigned_count || 0} assigned</div>
            <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-ink-muted sm:grid-cols-4">
              <span>Stage: <b className="text-ink">{operation.stage || "—"}</b></span>
              <span>Job: <code>{operation.jobId || "—"}</code></span>
              <span>Retries: <b className="text-ink">{operation.retries ?? 0}</b></span>
              <span>Worker: <b className="text-ink">{operation.workerStatus || "—"}</b></span>
            </div>
            {r.admin_reason && <div className="mt-1 text-xs text-ink-muted"><b>Admin note:</b> {r.admin_reason}</div>}
            {r.rejection_reason && <div className="mt-1 text-xs text-ink-muted"><b>Reason:</b> {r.rejection_reason}</div>}
          </div>;
        })}
      </div>}

      {msg && <div className="rounded-xl border border-line bg-surface-2 px-4 py-2.5 text-sm text-ink-muted">{msg}</div>}

      {leads.length > 0 && (
        <div className="card-pad flex flex-wrap gap-2">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={onlyEmail} onChange={(e) => setOnlyEmail(e.target.checked)} />
            Only with email
          </label>
          <button type="button" className="btn-secondary" onClick={() => setSelected(new Set(visible.map((l) => l.assignment_id)))}>
            Select all shown
          </button>
          <button type="button" className="btn-secondary" onClick={() => setSelected(new Set(leads.filter((l) => l.email).map((l) => l.assignment_id)))}>
            Select with email
          </button>
          <button type="button" className="btn-primary" disabled={!selected.size} onClick={() => setEmailOpen(true)}>
            Email proposal ({selected.size})
          </button>
        </div>
      )}

      <div className="space-y-2">
        {visible.map((l) => (
          <div key={l.assignment_id} className="card-pad data-card-hover flex gap-3">
            <input type="checkbox" className="mt-1" checked={selected.has(l.assignment_id)} onChange={() => toggle(l.assignment_id)} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <div className="font-semibold text-ink">{l.name}</div>
                {l.tier && <span className="pill bg-brand-50 text-brand-700 dark:bg-brand-500/10 dark:text-brand-300">Tier {l.tier}</span>}
                {l.opportunity_score != null && <span className="pill bg-signal-50 text-signal-700 dark:bg-signal-500/10 dark:text-signal-300">Opportunity {l.opportunity_score}</span>}
              </div>
              <div className="mt-1 text-sm text-ink-muted">{l.email || "No email"} · {l.phone || "No phone"} · {l.city || "—"}</div>
              {(l.evidence || l.why_now || l.signals) && <div className="mt-2 flex flex-wrap gap-1.5">{[...(Array.isArray(l.evidence) ? l.evidence : []), ...(Array.isArray(l.why_now) ? l.why_now : []), ...(Array.isArray(l.signals) ? l.signals : [])].slice(0,4).map((x: any, i: number) => <span key={i} className="rounded-md border border-line bg-surface-3/50 px-2 py-1 text-[10px] text-ink-muted">{typeof x === "string" ? x : x?.label || x?.reason || JSON.stringify(x)}</span>)}</div>}
              {(l.talking_points || l.pitch_line) && (
                <p className="mt-1 text-sm text-ink-muted"><span className="font-medium text-ink">Say this:</span> {l.talking_points || l.pitch_line}</p>
              )}
            </div>
          </div>
        ))}
      </div>

      {emailOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="card-pad w-full max-w-lg space-y-3 shadow-lift">
            <h2 className="text-lg font-semibold">Send proposal ({selected.size})</h2>
            <p className="text-xs text-ink-faint">Tags: {"{{company}}"} {"{{owner}}"} {"{{city}}"} {"{{pitch}}"}</p>
            <input className="input" value={subj} onChange={(e) => setSubj(e.target.value)} />
            <textarea className="input min-h-[160px] font-mono text-xs" value={body} onChange={(e) => setBody(e.target.value)} />
            <button disabled={sending} className="btn-primary w-full" onClick={sendProposal}>
              {sending ? "Sending…" : "Send via Resend / SMTP"}
            </button>
            <button className="btn-ghost w-full" onClick={() => setEmailOpen(false)}>Close</button>
          </div>
        </div>
      )}
    </div>
  );
}
