"use client";

import { useEffect, useState } from "react";
import { admin } from "@/lib/admin";
import { ErrorState, EmptyState } from "@/components/ui/States";
import { Icon } from "@/components/ui/Icon";
import { PageHeader, Notice } from "@/components/ui/Primitives";

export default function AdminIntelligencePage() {
  const [pack, setPack] = useState<any>(null);
  const [qa, setQa] = useState<any>(null);
  const [bad, setBad] = useState<any[]>([]);
  const [territories, setTerritories] = useState<any[]>([]);
  const [tenants, setTenants] = useState<any[]>([]);
  const [form, setForm] = useState({ vertical: "medspa", geo: "Hoboken", limit: 20 });
  const [terr, setTerr] = useState({ tenant_id: "", vertical: "medspa", geo_key: "hoboken nj" });
  const [qaForm, setQaForm] = useState({ lead_id: "", phone_ok: true, email_ok: true, business_open: true, notes: "" });
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState<Error | null>(null);

  async function load() {
    setError(null);
    const results = await Promise.allSettled([admin.qaStats(), admin.badLeads(), admin.territories(), admin.tenants()]);
    const [qaResult, badResult, territoryResult, tenantsResult] = results;
    if (qaResult.status === "fulfilled") setQa(qaResult.value);
    if (badResult.status === "fulfilled") setBad(badResult.value.reports || []);
    if (territoryResult.status === "fulfilled") setTerritories(territoryResult.value.territories || []);
    if (tenantsResult.status === "fulfilled") setTenants(tenantsResult.value.tenants || []);
    if (results.every((result) => result.status === "rejected")) setError(new Error("Quality telemetry is unavailable."));
  }

  useEffect(() => { void load(); }, []);

  async function action(label: string, fn: () => Promise<any>, success: string) {
    setBusy(label); setMessage(""); setError(null);
    try { await fn(); setMessage(success); await load(); }
    catch (e) { setError(e instanceof Error ? e : new Error(String(e))); }
    finally { setBusy(""); }
  }

  return (
    <div className="page">
      <PageHeader eyebrow="Control plane · quality moat" title="Intelligence & quality" description="Validate contact evidence, manage replacement signals and protect exclusive territory before clients feel the cost." />
      {error && <ErrorState error={error} onRetry={() => void load()} />}
      {message && <Notice tone="success">{message}</Notice>}

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{[["Samples", qa?.samples ?? "—"], ["Phone verified", qa?.phone_ok_pct != null ? `${qa.phone_ok_pct}%` : "—"], ["Email verified", qa?.email_ok_pct != null ? `${qa.email_ok_pct}%` : "—"], ["Business open", qa?.business_open_pct != null ? `${qa.business_open_pct}%` : "—"]].map(([label, value], index) => <div className="kpi" key={String(label)}><div className="flex items-center gap-2"><Icon name={index === 0 ? "leads" : "check"} size={15} className="text-brand-600" /><span className="label">{label}</span></div><div className="kpi-value mt-2">{String(value)}</div><div className="mt-1 text-xs text-ink-faint">Last 30 days</div></div>)}</section>

      <section className="grid gap-5 lg:grid-cols-[.9fr_1.1fr]">
        <div className="data-card p-5"><div className="flex items-start justify-between gap-3"><div><div className="section-kicker">Evidence sampling</div><h2 className="mt-1 text-lg font-semibold text-ink">Generate a QA pack</h2><p className="mt-1 text-sm text-ink-muted">Create a small review set before changing a client’s target or source mix.</p></div><Icon name="search" className="text-brand-600" /></div><div className="mt-4 grid gap-3 sm:grid-cols-2"><label className="label">Vertical<input className="input mt-1" value={form.vertical} onChange={(e) => setForm({ ...form, vertical: e.target.value })} /></label><label className="label">Geo<input className="input mt-1" value={form.geo} onChange={(e) => setForm({ ...form, geo: e.target.value })} /></label></div><button type="button" className="btn-primary mt-4" disabled={busy === "pack"} onClick={() => void action("pack", async () => { const next = await admin.samplePack(form); setPack(next); }, "Sample pack generated.")}>{busy === "pack" ? "Generating…" : "Generate sample pack"}</button>{pack && <div className="mt-4 rounded-xl border border-line bg-surface-3/40 p-3 text-xs text-ink-muted">{pack.count || pack.leads?.length || 0} leads ready for review. <a href="/admin/leads" className="font-semibold text-brand-600 hover:underline">Open inventory →</a></div>}</div>
        <div className="data-card p-5"><div className="flex items-start justify-between gap-3"><div><div className="section-kicker">Human verification</div><h2 className="mt-1 text-lg font-semibold text-ink">QA a lead</h2></div><Icon name="check" className="text-signal-600" /></div><div className="mt-4 grid gap-3 sm:grid-cols-2"><input aria-label="Lead ID" placeholder="Lead ID" className="input sm:col-span-2" value={qaForm.lead_id} onChange={(e) => setQaForm({ ...qaForm, lead_id: e.target.value })} />{(["phone_ok", "email_ok", "business_open"] as const).map((key) => <label key={key} className="flex items-center gap-2 rounded-xl border border-line bg-surface-3/40 px-3 py-2 text-xs font-semibold text-ink"><input type="checkbox" checked={qaForm[key]} onChange={(e) => setQaForm({ ...qaForm, [key]: e.target.checked })} />{key.replaceAll("_", " ")}</label>)}</div><button type="button" className="btn-secondary mt-4" disabled={busy === "qa" || !qaForm.lead_id} onClick={() => void action("qa", () => admin.qaSubmit(qaForm), "QA result saved.")}>{busy === "qa" ? "Saving…" : "Save QA result"}</button></div>
      </section>

      <section className="data-card overflow-hidden"><div className="flex items-start justify-between gap-3 border-b border-line px-5 py-4"><div><div className="section-kicker">Replacement queue</div><h2 className="mt-1 text-lg font-semibold text-ink">Bad leads and credits</h2></div><Icon name="leads" className="text-rust-500" /></div>{bad.length === 0 ? <EmptyState title="No open quality reports" hint="Replacement and credit decisions will appear here when a client reports an issue." /> : <div className="divide-y divide-line/70">{bad.map((report) => <div key={report.id} className="flex flex-wrap items-center justify-between gap-3 px-5 py-3.5"><div><div className="text-sm font-semibold text-ink">{report.reason || "Quality report"}</div><div className="mt-1 text-xs text-ink-muted">{report.detail || "No additional detail"} · {report.tenant_id?.slice(0, 8) || "Unknown client"}</div></div><div className="flex gap-2"><button type="button" className="btn-success" disabled={!!busy} onClick={() => void action(report.id, () => admin.resolveBadLead({ report_id: report.id, credit: true, tenant_id: report.tenant_id, assignment_id: report.assignment_id }), "Lead credited and queued for replacement.")}>Credit</button><button type="button" className="btn-secondary" disabled={!!busy} onClick={() => void action(report.id, () => admin.resolveBadLead({ report_id: report.id, credit: false }), "Report rejected.")}>Reject</button></div></div>)}</div>}</section>

      <section className="data-card p-5"><div className="flex items-start justify-between gap-3"><div><div className="section-kicker">Territory protection</div><h2 className="mt-1 text-lg font-semibold text-ink">Exclusive coverage</h2><p className="mt-1 text-sm text-ink-muted">Lock vertical + geography combinations so assignments stay intentional.</p></div><Icon name="territory" className="text-brand-600" /></div><div className="mt-4 grid gap-3 md:grid-cols-[1fr_1fr_1fr_auto]"><select aria-label="Client" className="input" value={terr.tenant_id} onChange={(e) => setTerr({ ...terr, tenant_id: e.target.value })}><option value="">Choose client…</option>{tenants.map((tenant) => <option key={tenant.id} value={tenant.id}>{tenant.name}</option>)}</select><input aria-label="Vertical" className="input" value={terr.vertical} onChange={(e) => setTerr({ ...terr, vertical: e.target.value })} placeholder="Vertical" /><input aria-label="Geo key" className="input" value={terr.geo_key} onChange={(e) => setTerr({ ...terr, geo_key: e.target.value })} placeholder="Geo key" /><button type="button" className="btn-primary" disabled={!!busy || !terr.tenant_id} onClick={() => void action("territory", () => admin.createTerritory(terr), "Territory locked exclusively.")}>Lock territory</button></div>{territories.length === 0 ? <EmptyState title="No exclusive territories" hint="Territory locks will be listed here once created." /> : <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{territories.slice(0, 12).map((territory) => <div key={territory.id} className="action-card"><div className="flex items-center justify-between gap-2"><span className="font-semibold text-ink">{territory.vertical}</span><Icon name="territory" size={15} className="text-brand-600" /></div><div className="mt-1 text-xs text-ink-muted">{territory.geo_key} · {territory.tenant_id?.slice(0, 8)}…</div></div>)}</div>}</section>
    </div>
  );
}
