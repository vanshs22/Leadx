"use client";

import { useLiveLoader } from "@/lib/live";
import { ErrorState, EmptyState, LoadingRows, PollingDeadlineState } from "@/components/ui/States";
import { OperationStateCard, toOperationState } from "@/components/ui/OperationState";
import { PageHeader, Panel, KeyValue, StatusBadge, Badge, Drawer, Notice } from "@/components/ui/Primitives";
import { Icon } from "@/components/ui/Icon";
import { TierBadge } from "@/components/leads/TierBadge";
import { useCallback, useMemo, useRef, useState } from "react";
import { crm } from "@/lib/api";
import { useCrmChange } from "@/lib/crmRealtime";
import { getTenantId } from "@/lib/tenant";

const WITHDRAWABLE_REQUEST_STATUSES = new Set(["pending_approval", "approved", "queued", "running"]);


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
  const closeEmail = useCallback(() => setEmailOpen(false), []);
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

  async function withdrawRequest(requestId: string) {
    if (!window.confirm("Withdraw this opportunity request? Queued or running work will be stopped and unused quota released.")) return;
    setLoading(true);
    setMsg("");
    try {
      const result = await crm.withdrawDiscoveryRequest(getTenantId(), requestId);
      activeRequestId.current = null;
      setMsg(result.message || "Opportunity request withdrawn.");
      await refreshRequests();
    } catch (caught: any) {
      setRequestError(caught);
    } finally {
      setLoading(false);
    }
  }

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


  const quotaLimit = Number(quota?.limit) || 0;
  const usedPct = quotaLimit ? Math.min(100, (Number(quota?.used) || 0) / quotaLimit * 100) : 0;
  const reservedPct = quotaLimit ? Math.min(100 - usedPct, (Number(quota?.reserved) || 0) / quotaLimit * 100) : 0;
  const leadWhy = (l: any): string[] =>
    [...(Array.isArray(l.evidence) ? l.evidence : []), ...(Array.isArray(l.why_now) ? l.why_now : []), ...(Array.isArray(l.signals) ? l.signals : [])]
      .slice(0, 4)
      .map((x: any) => (typeof x === "string" ? x : x?.label || x?.reason || JSON.stringify(x)));

  return (
    <div className="page">
      <PageHeader
        eyebrow="Discover"
        title="Search & outreach"
        description="Find businesses with a problem you can solve, not just a list of names."
        meta={<span className="tabular-nums">{quota ? `${quota.remaining ?? "—"} of ${quota.limit ?? "—"} leads available this month` : "Loading allowance…"}</span>}
        actions={<button type="button" aria-label="Refresh discovery requests" className="btn-secondary" onClick={refreshRequestState}><Icon name="refresh" size={14} />Refresh</button>}
      />

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel
          className="lg:col-span-2"
          title="New discovery request"
          description="Pick an opportunity recipe, then describe who and where. Requests are reviewed before delivery."
          id="discovery-form"
          footer={<>
            <span className="min-w-0"><span className="font-semibold text-ink">Evidence-first discovery:</span> source, fit, contactability and opportunity signals are evaluated before delivery.</span>
            <button disabled={loading} onClick={findLeads} className="btn-primary shrink-0" aria-busy={loading}>
              <Icon name="search" size={14} />{loading ? "Submitting…" : "Request opportunities"}
            </button>
          </>}
        >
          <div className="space-y-5">
            <fieldset>
              <legend className="field-label">Opportunity recipe</legend>
              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3" role="group" aria-label="Opportunity recipes">
                {Object.entries(RECIPES).map(([key, r]) => {
                  const active = recipe === key;
                  return (
                    <button key={key} type="button" aria-pressed={active} onClick={() => setRecipe(key)} className={`rounded-lg border px-3 py-2.5 text-left transition ${active ? "border-brand-500 bg-brand-50 ring-1 ring-brand-500/30 dark:border-brand-300/60 dark:bg-brand-500/15" : "border-line bg-surface-2 hover:border-line-strong hover:bg-surface-3"}`}>
                      <div className="flex items-center justify-between gap-2">
                        <span className={`text-[0.8125rem] font-semibold ${active ? "text-brand-800 dark:text-brand-100" : "text-ink"}`}>{r.label}</span>
                        {active ? <Icon name="check" size={14} className="shrink-0 text-brand-700 dark:text-brand-300" /> : null}
                      </div>
                      <div className="mt-0.5 text-xs leading-4 text-ink-faint">{r.hint}</div>
                    </button>
                  );
                })}
              </div>
              <p className="field-hint">
                Recipe: <span className="font-medium text-ink-muted">{activeRecipe.label}</span> · <span className="tabular-nums">{activeRecipe.keywords.length}</span> discovery signals added to your keywords.
              </p>
            </fieldset>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="field-label" htmlFor="discover-vertical">Who to find (vertical)</label>
                <input id="discover-vertical" className="input" value={vertical} onChange={(e) => setVertical(e.target.value)} />
              </div>
              <div>
                <label className="field-label" htmlFor="discover-geo">Where</label>
                <input id="discover-geo" className="input" value={geo} onChange={(e) => setGeo(e.target.value)} aria-describedby="discover-geo-hint" />
                <p id="discover-geo-hint" className="field-hint">Comma-separated cities or regions.</p>
              </div>
              <div className="sm:col-span-2">
                <label className="field-label" htmlFor="discover-keywords">Keywords</label>
                <input id="discover-keywords" className="input" value={keywords} onChange={(e) => setKeywords(e.target.value)} />
              </div>
              <div>
                <label className="field-label" htmlFor="discover-offer">Your offer category</label>
                <input id="discover-offer" className="input" value={offer} onChange={(e) => setOffer(e.target.value)} />
              </div>
              <div>
                <label className="field-label" htmlFor="discover-limit">Leads requested</label>
                <input id="discover-limit" className="input tabular-nums" type="number" min={1} max={500} value={limit} onChange={(e) => setLimit(Math.min(500, Math.max(1, Number(e.target.value) || 1)))} aria-describedby="discover-limit-hint" />
              <p className="field-hint">This is the batch size for this request, not your whole monthly allowance. LeadX keeps the remaining allowance available for later requests.</p>
              </div>
            </div>
          </div>
        </Panel>

        <Panel title="Lead allowance" description="This month's plan credits." id="discovery-quota">
          <div className="space-y-5">
            <div>
              <div className="stat-label">Available</div>
              <div className="stat-value">{quota?.remaining ?? "—"}</div>
              <div
                className="mt-4 flex h-2 overflow-hidden rounded-full bg-surface-3"
                role="meter"
                aria-label="Monthly lead allowance used"
                aria-valuemin={0}
                aria-valuemax={quotaLimit || 0}
                aria-valuenow={Number(quota?.used) || 0}
              >
                <span className="h-full bg-brand-700 dark:bg-brand-300" style={{ width: `${usedPct}%` }} />
                <span className="h-full bg-brand-300 dark:bg-brand-500/60" style={{ width: `${reservedPct}%` }} />
              </div>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-faint">
                <span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-brand-700 dark:bg-brand-300" aria-hidden="true" />Used</span>
                <span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-brand-300 dark:bg-brand-500/60" aria-hidden="true" />Reserved</span>
              </div>
            </div>
            <KeyValue columns={1} items={[
              { label: "Monthly lead allowance", value: <span className="tabular-nums">{quota?.limit ?? "—"}</span> },
              { label: "Used / reserved", value: <span className="tabular-nums">{quota ? `${quota.used} / ${quota.reserved}` : "—"}</span> },
              { label: "Available after reservations", value: <span className="tabular-nums">{quota?.remaining ?? "—"}</span> },
            ]} />
          </div>
        </Panel>
      </div>

      {requestError ? <ErrorState error={requestError} onRetry={refreshRequestState} /> : null}
      {pollingExpired ? <PollingDeadlineState onRefresh={refreshRequestState} /> : null}
      {msg && <Notice tone="neutral" onDismiss={() => setMsg("")}>{msg}</Notice>}
      {activeOperation ? (
        <OperationStateCard operation={activeOperation} title="Latest discovery request" onRefresh={refreshRequestState} />
      ) : null}

      <Panel title="Recent discovery requests" description="The six most recent requests for this workspace." bodyClassName="p-0" id="discovery-requests">
        {requestsLoading ? (
          <LoadingRows rows={4} cols={6} />
        ) : !requestError && requests.length === 0 ? (
          <EmptyState title="No discovery requests yet" hint="Choose a recipe and request opportunities to start a tenant-scoped run." />
        ) : requests.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="data-table min-w-[880px]">
              <caption className="sr-only">Recent discovery requests</caption>
              <thead>
                <tr className="[&>th]:bg-surface-3">
                  <th scope="col">Request</th>
                  <th scope="col">Status</th>
                  <th scope="col" className="!text-right">Requested</th>
                  <th scope="col" className="!text-right">Approved</th>
                  <th scope="col" className="!text-right">Assigned</th>
                  <th scope="col">Stage</th>
                  <th scope="col">Job</th>
                  <th scope="col" className="!text-right">Retries</th>
                  <th scope="col">Worker</th>
                  <th scope="col" className="!text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {requests.slice(0, 6).map((r: any) => {
                  const operation = toOperationState(r, { status: r.status || "queued", stage: r.status || "queued" });
                  const label = `${r.vertical} · ${(r.locations || []).join(", ") || "Any location"}`;
                  const note = r.admin_reason ? `Admin note: ${r.admin_reason}` : r.rejection_reason ? `Reason: ${r.rejection_reason}` : "";
                  return (
                    <tr key={r.id}>
                      <td className="max-w-[280px] !py-2.5">
                        <div className="truncate font-medium text-ink" title={label}>{label}</div>
                        {note ? <div className="mt-0.5 truncate text-xs text-ink-muted" title={note}>{note}</div> : null}
                      </td>
                      <td className="!py-2.5"><StatusBadge status={r.status} /></td>
                      <td className="!py-2.5 text-right tabular-nums">{r.requested_leads ?? "—"}</td>
                      <td className="!py-2.5 text-right tabular-nums">{r.approved_leads || "—"}</td>
                      <td className="!py-2.5 text-right tabular-nums">{r.assigned_count || 0}</td>
                      <td className="!py-2.5 text-xs capitalize text-ink-muted">{operation.stage ? String(operation.stage).replace(/_/g, " ") : "—"}</td>
                      <td className="max-w-[140px] !py-2.5"><code className="block truncate font-mono text-xs text-ink-muted" title={operation.jobId || undefined}>{operation.jobId || "—"}</code></td>
                      <td className="!py-2.5 text-right tabular-nums">{operation.retries ?? 0}</td>
                      <td className="!py-2.5 text-xs text-ink-muted">{operation.workerStatus || "—"}</td>
                      <td className="!py-2.5 text-right">
                        {WITHDRAWABLE_REQUEST_STATUSES.has(String(r.status || "").toLowerCase()) ? (
                          <button type="button" className="btn-ghost btn-sm text-rust-600 dark:text-rust-300" onClick={() => void withdrawRequest(r.id)} disabled={loading}>Withdraw</button>
                        ) : <span className="text-xs text-ink-faint">—</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="px-5 py-4 text-[0.8125rem] text-ink-faint">Requests are unavailable until the status check succeeds.</p>
        )}
      </Panel>

      {leads.length > 0 && (
        <Panel
          title="Results"
          description="Each lead shows the evidence behind it, so you know why it is worth a message."
          bodyClassName="p-0"
          actions={<>
            <label className="flex items-center gap-2 text-xs font-medium text-ink-muted">
              <input type="checkbox" className="h-4 w-4 rounded border-line-strong accent-brand-700" checked={onlyEmail} onChange={(e) => setOnlyEmail(e.target.checked)} />
              Only with email
            </label>
            <button type="button" className="btn-ghost btn-sm" onClick={() => setSelected(new Set(visible.map((l) => l.assignment_id)))}>
              Select all shown
            </button>
            <button type="button" className="btn-ghost btn-sm" onClick={() => setSelected(new Set(leads.filter((l) => l.email).map((l) => l.assignment_id)))}>
              Select with email
            </button>
            <button type="button" className="btn-primary btn-sm" disabled={!selected.size} onClick={() => setEmailOpen(true)}>
              Email proposal (<span className="tabular-nums">{selected.size}</span>)
            </button>
          </>}
        >
          <ul className="divide-y divide-line">
            {visible.map((l) => {
              const why = leadWhy(l);
              const checked = selected.has(l.assignment_id);
              return (
                <li key={l.assignment_id} className={`flex gap-3 px-5 py-3.5 transition-colors hover:bg-surface-3/50 ${checked ? "bg-brand-50/70 dark:bg-brand-500/10" : ""}`}>
                  <input type="checkbox" className="mt-1 h-4 w-4 shrink-0 rounded border-line-strong accent-brand-700" aria-label={`Select ${l.name || "lead"}`} checked={checked} onChange={() => toggle(l.assignment_id)} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate font-semibold text-ink" title={l.name}>{l.name}</span>
                      {l.tier && <TierBadge tier={l.tier} />}
                      {l.opportunity_score != null && <Badge tone={Number(l.opportunity_score) >= 75 ? "accent" : "neutral"}><span className="font-medium opacity-80">Opportunity</span><span className="tabular-nums">{l.opportunity_score}</span></Badge>}
                    </div>
                    <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-ink-muted">
                      <span className="truncate" title={l.email || undefined}>{l.email || "No email"}</span>
                      <span className="tabular-nums">{l.phone || "No phone"}</span>
                      <span>{l.city || "—"}</span>
                    </div>
                    {why.length > 0 && (
                      <div className="mt-2">
                        <div className="eyebrow text-signal-700 dark:text-signal-300">Why this lead</div>
                        <div className="mt-1 flex flex-wrap gap-1.5">{why.map((x, i) => <span key={i} className="badge-neutral max-w-full truncate font-medium" title={x}>{x}</span>)}</div>
                      </div>
                    )}
                    {(l.talking_points || l.pitch_line) && (
                      <p className="mt-2 text-[0.8125rem] leading-5 text-ink-muted"><span className="font-medium text-ink">Say this:</span> {l.talking_points || l.pitch_line}</p>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </Panel>
      )}

      <Drawer
        open={emailOpen}
        title={`Send proposal (${selected.size})`}
        onClose={closeEmail}
        footer={
          <div className="flex flex-wrap items-center justify-end gap-2">
            <button className="btn-ghost" onClick={() => setEmailOpen(false)}>Close</button>
            <button disabled={sending} className="btn-primary" onClick={sendProposal}>
              {sending ? "Sending…" : "Send via Resend / SMTP"}
            </button>
          </div>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="field-label" htmlFor="discover-email-subject">Subject</label>
            <input id="discover-email-subject" className="input" value={subj} onChange={(e) => setSubj(e.target.value)} />
          </div>
          <div>
            <label className="field-label" htmlFor="discover-email-body">Body (HTML)</label>
            <textarea id="discover-email-body" className="textarea min-h-[200px] font-mono text-xs" value={body} onChange={(e) => setBody(e.target.value)} aria-describedby="discover-email-tags" />
            <p id="discover-email-tags" className="field-hint">Tags: <code className="font-mono">{"{{company}}"} {"{{owner}}"} {"{{city}}"} {"{{pitch}}"}</code></p>
          </div>
        </div>
      </Drawer>
    </div>
  );
}
