"use client";

import { useEffect, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { admin } from "@/lib/admin";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/States";
import { FreshnessNote } from "@/components/ui/SurfaceMeta";
import { PageHeader } from "@/components/ui/Primitives";

function IcpsInner() {
  const params = useSearchParams();
  const preTenant = params.get("tenant") || "";
  const [tenants, setTenants] = useState<any[]>([]);
  const [icps, setIcps] = useState<any[]>([]);
  const [form, setForm] = useState({ tenant_id: preTenant, name: "default", vertical: "medspa", geo: "Hoboken NJ", keywords: "med spa, medical spa", exclusivity: "exclusive" });
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  function load() {
    setLoading(true); setError(null);
    return admin.icps(preTenant || undefined).then((d) => setIcps(d.icps || [])).catch(setError).finally(() => setLoading(false));
  }

  useEffect(() => {
    admin.tenants().then((d) => setTenants(d.tenants || [])).catch(setError);
    void load();
  }, [preTenant]);

  async function create() {
    try {
      await admin.createIcp({ tenant_id: form.tenant_id, name: form.name, vertical: form.vertical, geo: form.geo.split(",").map((s) => s.trim()).filter(Boolean), keywords: form.keywords.split(",").map((s) => s.trim()).filter(Boolean), exclusivity: form.exclusivity });
      setMsg("ICP created");
      void load();
    } catch (caught: any) { setMsg(caught.message || "Could not create ICP"); }
  }

  async function toggle(id: string, active: boolean) {
    try { await admin.updateIcp(id, { active: !active }); void load(); }
    catch (caught: any) { setMsg(caught.message || "Could not update ICP"); }
  }

  if (loading) return <div className="page"><h1 className="page-title">ICPs</h1><LoadingRows rows={5} cols={4} /></div>;
  if (error) return <div className="page"><ErrorState error={error} onRetry={load} /></div>;

  return <div className="page">
    <PageHeader eyebrow="Admin targeting" title="ICPs" description="Vertical, geography, keyword, exclusivity, and active-state targets per client." />
    <div className="mb-3"><FreshnessNote label="Control-plane snapshot" detail={`${icps.length} ICPs loaded`} /></div>
    <div className="grid gap-3 premium-card p-5 sm:grid-cols-2">
      <select className="rounded-lg border border-line bg-surface px-3 py-2 text-sm" value={form.tenant_id} onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}><option value="">Client…</option>{tenants.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select>
      <input placeholder="Vertical (medspa)" className="rounded-lg border border-line bg-surface px-3 py-2 text-sm" value={form.vertical} onChange={(e) => setForm({ ...form, vertical: e.target.value })} />
      <input placeholder="Geo cities (comma-separated)" className="rounded-lg border border-line bg-surface px-3 py-2 text-sm" value={form.geo} onChange={(e) => setForm({ ...form, geo: e.target.value })} />
      <input placeholder="Keywords" className="rounded-lg border border-line bg-surface px-3 py-2 text-sm" value={form.keywords} onChange={(e) => setForm({ ...form, keywords: e.target.value })} />
      <select className="rounded-lg border border-line bg-surface px-3 py-2 text-sm" value={form.exclusivity} onChange={(e) => setForm({ ...form, exclusivity: e.target.value })}><option value="exclusive">Exclusive</option><option value="shared">Shared</option></select>
      <button aria-label="Create ICP" onClick={create} disabled={!form.tenant_id || !form.vertical} className="rounded-lg bg-brand-600 px-4 py-2 text-sm text-white disabled:opacity-50">Create ICP</button>
    </div>
    {msg && <p className="rounded-xl border border-line bg-surface-2 px-4 py-2.5 text-sm text-ink-muted" role="status">{msg}</p>}
    <div className="overflow-hidden rounded-xl border border-line"><table className="w-full text-left text-sm"><thead className="border-b border-line bg-surface/80 text-xs uppercase text-ink-faint"><tr><th className="px-4 py-3">Vertical</th><th className="px-4 py-3">Geo</th><th className="px-4 py-3">Exclusivity</th><th className="px-4 py-3">Active</th><th className="px-4 py-3">Tenant</th></tr></thead><tbody className="divide-y divide-line">{icps.map((i) => <tr key={i.id}><td className="px-4 py-3 font-medium text-ink">{i.vertical}</td><td className="px-4 py-3 text-ink-faint">{(i.geo || []).join(", ") || "—"}</td><td className="px-4 py-3 text-ink-faint">{i.exclusivity || "unavailable"}</td><td className="px-4 py-3"><button aria-label={`Toggle ${i.vertical}`} onClick={() => toggle(i.id, i.active)} className={`rounded px-2 py-0.5 text-xs ${i.active ? "bg-signal-900/50 text-signal-300" : "bg-surface-3 text-ink-faint"}`}>{i.active ? "on" : "off"}</button></td><td className="px-4 py-3 font-mono text-xs text-ink-muted">{i.tenant_id?.slice(0, 8) || "unavailable"}</td></tr>)}{icps.length === 0 && <tr><td colSpan={5} className="px-4 py-8"><EmptyState title="No ICPs for this view" hint="Choose a client and create its first vertical and geography target." /></td></tr>}</tbody></table></div>
  </div>;
}

export default function AdminIcpsPage() { return <Suspense fallback={<div className="page"><LoadingRows rows={4} cols={3} /></div>}><IcpsInner /></Suspense>; }
