"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { competitive, crmIntelligence } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { useLiveLoader } from "@/lib/live";
import { ErrorState, EmptyState, LoadingRows, PollingDeadlineState } from "@/components/ui/States";
import { Badge, PageHeader, Panel, StatCard, StatGrid } from "@/components/ui/Primitives";
import { Icon } from "@/components/ui/Icon";
import { FreshnessNote } from "@/components/ui/SurfaceMeta";

export default function TodayPage() {
  const [brief, setBrief] = useState<any>(null); const [board, setBoard] = useState<any>(null); const [error, setError] = useState<unknown>(null); const [busy, setBusy] = useState(""); const [deadline, setDeadline] = useState(false); const [refreshKey, setRefreshKey] = useState(0);
  const tenantId = getTenantId();
  const load = useCallback(async () => { if (!tenantId) return; try { const [daily, leads, quality] = await Promise.all([crmIntelligence.dailyBrief(tenantId), competitive.today(tenantId, 5), competitive.quality(tenantId)]); setBrief(daily); setBoard({ ...leads, quality }); setError(null); } catch (caught) { setError(caught); } }, [tenantId]);
  useLiveLoader(load, 30_000, [refreshKey], { onDeadline: () => setDeadline(true) });
  useCrmChange(() => { void load(); }, { tables: ["lead_assignments", "lead_outcome_events", "lead_activities", "deals", "notifications"] });
  const refresh = () => { setDeadline(false); setRefreshKey((value) => value + 1); };
  async function outcome(id: string, outcomeName: string) { if (!tenantId) return; setBusy(id + outcomeName); try { await competitive.outcome(tenantId, id, outcomeName); setBoard((current: any) => ({ ...current, leads: (current?.leads || []).filter((row: any) => row.id !== id) })); } catch (caught) { setError(caught); } finally { setBusy(""); } }
  const leads = board?.leads || [];
  const loadingBoard = !board && !error && Boolean(tenantId);
  const kpi = (value: unknown) => (value == null ? "—" : typeof value === "number" ? value.toLocaleString() : String(value));
  return <div className="page">
    <PageHeader
      eyebrow="Daily command center"
      title="Today"
      description="The five leads worth acting on first, with the evidence behind each one."
      actions={<button type="button" aria-label="Refresh today brief" className="btn-secondary" onClick={refresh}><Icon name="refresh" size={14} />Refresh</button>}
      meta={<FreshnessNote label="Live action queue" detail={`${leads.length} leads · read-side refresh only`} />}
    />
    {deadline ? <PollingDeadlineState onRefresh={refresh} /> : null}
    {error ? <ErrorState error={error} onRetry={refresh} /> : null}
    <StatGrid cols={4}>
      <StatCard label="New leads" value={kpi(brief?.new_assignments)} hint="Assigned since your last brief" tone="brand" icon={<Icon name="leads" size={16} />} />
      <StatCard label="Data quality" value={board?.quality?.data_quality_pct == null ? "—" : `${board.quality.data_quality_pct}%`} hint="Verified, contactable records" tone="success" icon={<Icon name="check" size={16} />} />
      <StatCard label="Credits returned" value={kpi(board?.quality?.credits_returned)} hint="Refunded for bad data" icon={<Icon name="billing" size={16} />} />
      <StatCard label="Won recently" value={kpi(brief?.won_deals)} hint="Closed deals" tone="accent" icon={<Icon name="revenue" size={16} />} />
    </StatGrid>
    <Panel
      id="today-queue"
      title="Call these now"
      description="Outcomes write to the existing lead outcome API; they do not trigger provider work."
      actions={leads.length ? <Badge tone="brand" className="tabular-nums">{leads.length} in queue</Badge> : null}
      bodyClassName="p-0"
    >
      {loadingBoard ? <LoadingRows rows={5} cols={3} /> : null}
      {!loadingBoard && !leads.length ? <EmptyState title="No new leads need action right now" hint="The daily brief returned no actionable lead rows for this workspace." icon={<Icon name="check" size={18} />} /> : null}
      {leads.length ? <ol className="divide-y divide-line" aria-label="Leads to act on today">{leads.map((row: any, index: number) => {
        const tier = String(row.tier || "");
        const isTierA = /^(tier[_\s-]?)?a$/i.test(tier);
        return <li key={row.id} className="px-5 py-4">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
            <div className="flex min-w-0 gap-3">
              <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-line bg-surface-3 text-xs font-semibold tabular-nums text-ink-muted" aria-hidden="true">{index + 1}</span>
              <div className="min-w-0">
                <Link href={`/leads/${row.id}`} className="text-[0.9375rem] font-semibold text-ink hover:text-brand-700 dark:hover:text-brand-300">{row.lead?.name || "Local business"}</Link>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-ink-muted">
                  <span>{row.lead?.city}{row.lead?.state ? `, ${row.lead.state}` : ""}</span>
                  {tier ? <Badge tone={isTierA ? "accent" : "neutral"}>{tier.replace(/_/g, " ")}</Badge> : <span className="text-ink-faint">tier unavailable</span>}
                </div>
                {row.top_signal ? <div className="mt-3 inline-flex max-w-full items-start gap-2 rounded-lg border border-signal-200 bg-signal-50 px-3 py-2 text-[0.8125rem] text-signal-800 dark:border-signal-400/30 dark:bg-signal-400/10 dark:text-signal-100"><Icon name="spark" size={14} className="mt-0.5 shrink-0" /><span><span className="font-semibold">Why now:</span> {row.top_signal.signal_type?.replace(/_/g, " ") || "signal returned"}</span></div> : <div className="mt-3 text-xs text-ink-faint">No top signal returned by the API.</div>}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2 xl:max-w-[520px] xl:justify-end">
              {row.lead?.phone_e164 ? <a className="btn-primary btn-sm" href={`tel:${row.lead.phone_e164}`} aria-label={`Call ${row.lead?.name || "lead"}`}><Icon name="phone" size={14} />Call</a> : null}
              {row.lead?.website ? <a className="btn-secondary btn-sm" target="_blank" rel="noreferrer" href={row.lead.website} aria-label={`Open website for ${row.lead?.name || "lead"}`}><Icon name="external" size={14} />Site</a> : null}
              <span className="hidden h-5 w-px bg-line sm:block" aria-hidden="true" />
              {["no_answer", "booked", "not_interested", "won"].map((name) => <button type="button" key={name} aria-label={`Record ${name.replace(/_/g, " ")} for ${row.lead?.name || "lead"}`} className={`${name === "won" ? "btn-success" : "btn-secondary"} btn-sm capitalize`} disabled={busy === row.id + name} onClick={() => outcome(row.id, name)}>{busy === row.id + name ? "Saving…" : name.replace(/_/g, " ")}</button>)}
            </div>
          </div>
        </li>;
      })}</ol> : null}
    </Panel>
  </div>;
}
