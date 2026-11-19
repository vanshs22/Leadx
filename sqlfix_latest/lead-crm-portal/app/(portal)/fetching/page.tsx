"use client";

import { useCallback, useEffect, useState } from "react";
import { crmFetching, type FetchVerification, type FetchingMetrics } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { Badge, DataTable, FilterBar, PageHeader, StatCard } from "@/components/ui/Primitives";
import { DataState, EmptyState, LoadingRows } from "@/components/ui/States";

const PROVIDERS = ["direct", "scrapling", "browser", "crawl4ai", "firecrawl"];
const pct = (v: number | null | undefined) => (v == null ? "n/a" : `${v}%`);

export default function FetchingPage() {
  const tenantId = getTenantId();
  const [m, setM] = useState<FetchingMetrics | null>(null), [queue, setQueue] = useState<FetchVerification[]>([]), [loading, setLoading] = useState(true), [error, setError] = useState<unknown>(null);
  const [from, setFrom] = useState(""), [to, setTo] = useState(""), [provider, setProvider] = useState(""), [category, setCategory] = useState(""), [source, setSource] = useState("");
  const load = useCallback(async () => {
    if (!tenantId) return;
    setLoading(true); setError(null);
    try {
      const [metrics, ver] = await Promise.all([
        crmFetching.metrics(tenantId, { date_from: from ? new Date(from).toISOString() : undefined, date_to: to ? new Date(`${to}T23:59:59`).toISOString() : undefined, provider, category, source }),
        crmFetching.verification(tenantId),
      ]);
      setM(metrics); setQueue(ver.items);
    } catch (caught) { setError(caught); } finally { setLoading(false); }
  }, [tenantId, from, to, provider, category, source]);
  useEffect(() => { void load(); }, [load]);
  const act = async (id: string, action: "resolved" | "dismissed" | "retry") => { if (tenantId) { await crmFetching.resolve(tenantId, id, action); void load(); } };

  return (
    <div className="page">
      <PageHeader eyebrow="Data engine" title="Fetching intelligence" description="How website acquisition performs: fetch success, bot-protection blocks, alternative-source recovery, cost and tokens. Challenges are classified and parked for a person, never bypassed." />
      <FilterBar onClear={() => { setFrom(""); setTo(""); setProvider(""); setCategory(""); setSource(""); }}>
        <label className="sr-only" htmlFor="f-from">From date</label><input id="f-from" type="date" className="input md:w-40" value={from} onChange={(e) => setFrom(e.target.value)} />
        <label className="sr-only" htmlFor="f-to">To date</label><input id="f-to" type="date" className="input md:w-40" value={to} onChange={(e) => setTo(e.target.value)} />
        <label className="sr-only" htmlFor="f-provider">Provider</label>
        <select id="f-provider" className="select md:w-44" value={provider} onChange={(e) => setProvider(e.target.value)}><option value="">All providers</option>{PROVIDERS.map((p) => <option key={p} value={p}>{p}</option>)}</select>
        <label className="sr-only" htmlFor="f-category">Failure category</label>
        <select id="f-category" className="select md:w-52" value={category} onChange={(e) => setCategory(e.target.value)}><option value="">All outcomes</option>{Object.keys(m?.failure_categories || {}).map((c) => <option key={c} value={c}>{c}</option>)}</select>
        <label className="sr-only" htmlFor="f-source">Discovery source</label>
        <select id="f-source" className="select md:w-48" value={source} onChange={(e) => setSource(e.target.value)}><option value="">All sources</option>{(m?.sources || []).map((s) => <option key={s} value={s}>{s}</option>)}</select>
      </FilterBar>
      <DataState loading={loading && !m} error={error} isEmpty={!m || m.websites_attempted === 0} empty={<div className="card"><EmptyState title="No fetch activity in this window" hint="Run a discovery with enrichment, or widen the filters." /></div>} onRetry={() => void load()} skeleton={<div className="card"><LoadingRows rows={6} cols={4} /></div>}>
        {m && (<>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="Websites attempted" value={m.websites_attempted} />
            <StatCard label="Successful fetches" value={m.successful_fetches} hint={pct(m.fetch_success_pct)} tone="success" />
            <StatCard label="Bot-protection blocked" value={m.captcha_blocked} hint="Cloudflare, captcha, 403/429" tone="warn" />
            <StatCard label="HTTP / timeout / DNS failures" value={`${m.http_failures} / ${m.timeout_failures} / ${m.dns_failures}`} tone="danger" />
            <StatCard label="Alternative-source recoveries" value={m.alt_source_recoveries} hint={`Fetch + recovery: ${pct(m.acquisition_success_pct)}`} tone="success" />
            <StatCard label="Extraction completeness" value={pct(m.avg_extraction_completeness_pct)} />
            <StatCard label="Avg fetch time" value={m.avg_fetch_seconds == null ? "n/a" : `${m.avg_fetch_seconds}s`} />
            <StatCard label="Cost / enriched lead" value={m.cost.avg_per_enriched_lead == null ? "n/a" : `${m.cost.avg_per_enriched_lead} cr`} hint={`${m.ai_tokens} AI tokens`} />
          </div>
          <h2 className="mt-6 mb-2 text-sm font-semibold">Provider success</h2>
          <DataTable caption="Success rate per fetch provider"><thead><tr><th>Provider</th><th>Attempts</th><th>Success</th><th>Avg time</th><th>Cost (credits)</th></tr></thead>
            <tbody>{Object.entries(m.by_provider).map(([name, p]) => <tr key={name}><td>{name}</td><td>{p.attempts}</td><td>{pct(p.success_rate_pct)}</td><td>{p.avg_ms == null ? "n/a" : `${p.avg_ms} ms`}</td><td>{p.cost}</td></tr>)}</tbody></DataTable>
          <h2 className="mt-6 mb-2 text-sm font-semibold">Most problematic domains</h2>
          <DataTable caption="Domains with the most failed attempts"><thead><tr><th>Domain</th><th>Attempts</th><th>Failures</th><th>Last failure</th></tr></thead>
            <tbody>{m.problem_domains.map((d) => <tr key={d.domain}><td>{d.domain}</td><td>{d.attempts}</td><td>{d.failures}</td><td><Badge tone="warn">{d.last_category || "n/a"}</Badge></td></tr>)}</tbody></DataTable>
          <h2 className="mt-6 mb-2 text-sm font-semibold">Needs a person ({queue.length})</h2>
          {queue.length === 0 ? <div className="card"><EmptyState title="No challenged sites waiting" hint="Sites that show a bot-protection challenge appear here so someone can open them by hand." /></div> : (
            <DataTable caption="Sites waiting for manual verification"><thead><tr><th>Site</th><th>Challenge</th><th>Seen</th><th>Action</th></tr></thead>
              <tbody>{queue.map((q) => <tr key={q.id}><td><a className="link" href={`https://${q.domain}`} target="_blank" rel="noopener noreferrer">{q.domain}</a></td><td><Badge tone="warn">{q.challenge || q.category}</Badge></td><td>{new Date(q.created_at).toLocaleString()}</td>
                <td className="space-x-2"><button className="btn btn-sm" onClick={() => void act(q.id, "resolved")}>Checked by hand</button><button className="btn btn-sm" onClick={() => void act(q.id, "retry")}>Retry later</button><button className="btn btn-sm" onClick={() => void act(q.id, "dismissed")}>Dismiss</button></td></tr>)}</tbody></DataTable>)}
        </>)}
      </DataState>
    </div>
  );
}
