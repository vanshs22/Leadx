"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { competitive, crmIntelligence } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { useLiveLoader } from "@/lib/live";
import { ErrorState, EmptyState, PollingDeadlineState } from "@/components/ui/States";
import { PageHeader } from "@/components/ui/Primitives";
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
  return <div className="page space-y-5"><PageHeader eyebrow="Daily command center" title="Today" description="The five leads worth acting on first, with the evidence behind each one." actions={<button type="button" aria-label="Refresh today brief" className="btn-secondary" onClick={refresh}>Refresh</button>} meta={<FreshnessNote label="Live action queue" detail={`${leads.length} leads · read-side refresh only`} />} />
    {deadline ? <PollingDeadlineState onRefresh={refresh} /> : null}{error ? <ErrorState error={error} onRetry={refresh} /> : null}
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{[["New leads", brief?.new_assignments ?? "—"], ["Data quality", board?.quality?.data_quality_pct == null ? "—" : `${board.quality.data_quality_pct}%`], ["Credits returned", board?.quality?.credits_returned ?? "—"], ["Won recently", brief?.won_deals ?? "—"]].map(([label, value]) => <div className="kpi" key={String(label)}><div className="label">{label}</div><div className="kpi-value mt-2">{value}</div></div>)}</div>
    <section className="card overflow-hidden"><div className="border-b border-line px-5 py-4"><h2 className="font-semibold">Call these now</h2><p className="mt-1 text-xs text-ink-faint">Outcomes write to the existing lead outcome API; they do not trigger provider work.</p></div><div className="divide-y divide-line">{leads.map((row: any) => <article key={row.id} className="p-5"><div className="flex flex-wrap items-start justify-between gap-4"><div><Link href={`/leads/${row.id}`} className="text-base font-semibold hover:text-brand-600">{row.lead?.name || "Local business"}</Link><div className="mt-1 text-xs text-ink-muted">{row.lead?.city}{row.lead?.state ? `, ${row.lead.state}` : ""} · {row.tier || "tier unavailable"}</div>{row.top_signal ? <div className="mt-3 rounded-xl border border-brand-200 bg-brand-50 px-3 py-2 text-sm text-ink">Why now: {row.top_signal.signal_type?.replace(/_/g, " ") || "signal returned"}</div> : <div className="mt-3 text-xs text-ink-faint">No top signal returned by the API.</div>}</div><div className="flex flex-wrap gap-2">{row.lead?.phone_e164 ? <a className="btn-primary" href={`tel:${row.lead.phone_e164}`}>Call</a> : null}{row.lead?.website ? <a className="btn-secondary" target="_blank" rel="noreferrer" href={row.lead.website}>Site</a> : null}{["no_answer", "booked", "not_interested", "won"].map((name) => <button type="button" key={name} aria-label={`Record ${name.replace(/_/g, " ")} for ${row.lead?.name || "lead"}`} className={name === "won" ? "btn-primary" : "btn-secondary"} disabled={busy === row.id + name} onClick={() => outcome(row.id, name)}>{busy === row.id + name ? "Saving…" : name.replace(/_/g, " ")}</button>)}</div></div></article>)}{!leads.length ? <EmptyState title="No new leads need action right now" hint="The daily brief returned no actionable lead rows for this workspace." /> : null}</div></section>
  </div>;
}
