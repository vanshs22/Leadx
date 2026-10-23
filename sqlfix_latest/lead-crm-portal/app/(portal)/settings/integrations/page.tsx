"use client";

import { useEffect, useMemo, useState } from "react";
import { useCrmChange } from "@/lib/crmRealtime";
import { crmIntegrations } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/States";
import { FreshnessNote, UnavailableNote } from "@/components/ui/SurfaceMeta";
import { PageHeader, Notice } from "@/components/ui/Primitives";

const BYOK_FIELDS: Record<string, Record<string, string>> = {
  gmail: { access_token: "" }, google_calendar: { access_token: "" }, google_drive: { access_token: "" }, google_sheets: { access_token: "" },
  outlook: { access_token: "" }, microsoft_calendar: { access_token: "" }, microsoft_teams: { access_token: "" }, slack: { access_token: "" },
  hubspot: { access_token: "" }, salesforce: { access_token: "", instance_url: "https://your-domain.my.salesforce.com" },
  pipedrive: { api_key: "" }, close: { api_key: "" }, calcom: { api_key: "" }, notion: { api_key: "" }, airtable: { api_key: "" }, asana: { api_key: "" }, clickup: { api_key: "" }, linear: { api_key: "" },
  webhook: { url: "https://example.com/webhook", secret: "" }, zapier: { url: "https://hooks.zapier.com/..." }, make: { url: "https://hook.us1.make.com/..." }, n8n: { url: "https://example.com/webhook" }, generic_http: { url: "https://api.example.com/health", headers: "{}" },
};

export default function IntegrationsPage() {
  const [catalog, setCatalog] = useState<any[]>([]);
  const [connections, setConnections] = useState<any[]>([]);
  const [provider, setProvider] = useState("gmail");
  const [mode, setMode] = useState<"platform" | "byok">("platform");
  const [name, setName] = useState("");
  const [config, setConfig] = useState<Record<string, string>>({});
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [msg, setMsg] = useState("");
  const tenant = getTenantId();

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const r = await crmIntegrations.catalog(tenant);
      setCatalog(r.providers || []);
      setConnections(r.connections || []);
    } catch (caught) {
      setError(caught);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);
  useCrmChange(() => { void load(); }, { tables: ["integrations", "tenant_integrations"] });
  const selected = useMemo(() => catalog.find((x) => x.provider === provider), [catalog, provider]);
  const filtered = catalog.filter((p) => `${p.name} ${p.purpose} ${p.category}`.toLowerCase().includes(query.toLowerCase()));

  function select(p: any) {
    setProvider(p.provider);
    setMode("platform");
    setConfig(BYOK_FIELDS[p.provider] || {});
    setName("");
    setMsg("");
  }

  async function connect() {
    setBusy(true); setMsg("");
    try {
      if (mode === "platform") {
        const r = await crmIntegrations.oauthStart(tenant, provider);
        window.location.href = r.authorization_url;
        return;
      }
      const parsed: Record<string, any> = { ...config };
      if (typeof parsed.headers === "string") {
        try { parsed.headers = JSON.parse(parsed.headers); } catch { throw new Error("Headers must be valid JSON"); }
      }
      await crmIntegrations.saveConnection(tenant, { provider, config: parsed, metadata: { source: "user_byok" }, connection_name: name || undefined, auth_mode: "byok" });
      await load();
      setMsg("BYOK connection saved securely. Credentials are not shown again.");
    } catch (caught: any) {
      setMsg(caught.message || "Connection failed");
    } finally { setBusy(false); }
  }

  async function test(id: string) {
    setBusy(true); setMsg("");
    try {
      const r = await crmIntegrations.testConnection(tenant, id);
      setMsg(r.ok ? "Connection healthy." : r.error || r.message || "Connection test failed.");
      await load();
    } catch (caught: any) { setMsg(caught.message || "Connection test failed"); }
    finally { setBusy(false); }
  }

  if (loading) return <div className="page"><h1 className="page-title">Integrations</h1><LoadingRows rows={5} cols={3} /></div>;
  if (error) return <div className="page"><ErrorState error={error} onRetry={load} /></div>;

  return <div className="page max-w-7xl space-y-5">
    <PageHeader eyebrow="LeadX Connect" title="Integrations" description="Connect managed OAuth or your own credentials. Credentials stay server-side and are never rendered back to the browser." actions={<button className="btn-secondary" onClick={() => void load()} disabled={loading}>Refresh</button>} />
    <div className="mb-3"><FreshnessNote label="Connector catalog" detail={`${catalog.length} providers available`} /></div>
    {msg && <Notice tone={msg.includes("failed") || msg.includes("Failed") ? "danger" : "neutral"}>{msg}</Notice>}
    <div className="grid gap-5 lg:grid-cols-[1.1fr_.9fr]">
      <section className="card-pad"><div className="flex items-center justify-between gap-3"><h2 className="text-sm font-semibold">Connector catalog</h2><span className="pill bg-brand-50 text-brand-800 dark:bg-brand-500/15 dark:text-brand-200">{catalog.length} connectors</span></div><input className="input mt-4" placeholder="Search connectors by purpose or provider…" value={query} onChange={(e) => setQuery(e.target.value)} />{filtered.length ? <div className="mt-4 grid gap-2 sm:grid-cols-2">{filtered.map((p) => <button aria-label={`Select ${p.name}`} key={p.provider} onClick={() => select(p)} className={`rounded-xl border p-3 text-left transition ${provider === p.provider ? "border-brand-400 bg-brand-50/70 dark:bg-brand-500/10" : "border-line bg-surface-2 hover:border-brand-300"}`}><div className="font-semibold text-sm text-ink">{p.name}</div><div className="mt-1 text-xs text-ink-muted">{p.purpose || "Provider purpose unavailable"}</div><div className="mt-2 text-[10px] uppercase tracking-wide text-ink-faint">{p.category || "Category unavailable"} · {p.auth || "Auth unavailable"}</div></button>)}</div> : <EmptyState title="No connectors match" hint="Try a broader provider or purpose search." />}</section>
      <section className="card-pad"><div className="label">Connect account</div><h2 className="mt-1 text-lg font-semibold text-ink">{selected?.name || provider}</h2><div className="mt-4 grid grid-cols-2 rounded-xl border border-line bg-surface-2 p-1"><button aria-label="Use LeadX OAuth" onClick={() => setMode("platform")} className={`rounded-lg px-3 py-2 text-sm ${mode === "platform" ? "bg-brand-400 text-black" : "text-ink-muted"}`}>LeadX OAuth</button><button aria-label="Use BYOK" onClick={() => { setMode("byok"); setConfig(BYOK_FIELDS[provider] || {}); }} className={`rounded-lg px-3 py-2 text-sm ${mode === "byok" ? "bg-brand-400 text-black" : "text-ink-muted"}`}>My BYOK</button></div>{mode === "byok" && <label className="mt-4 block text-xs font-semibold text-ink-muted">Connection name<input className="input mt-1" placeholder="e.g. Sales Gmail" value={name} onChange={(e) => setName(e.target.value)} /></label>}{mode === "byok" && <div className="mt-4 space-y-3">{Object.entries(config).map(([k, v]) => <label key={k} className="block text-xs font-semibold text-ink-muted">{k.replace(/_/g, " ")}<input className="input mt-1" type={k.includes("token") || k.includes("key") || k.includes("secret") ? "password" : "text"} value={v} onChange={(e) => setConfig({ ...config, [k]: e.target.value })} /></label>)}</div>}<button aria-label="Connect selected provider" className="btn-primary mt-4 w-full" disabled={busy} onClick={connect}>{busy ? "Connecting…" : mode === "platform" ? `Connect ${selected?.name || provider}` : "Save encrypted BYOK connection"}</button><p className="mt-3 text-[11px] leading-4 text-ink-faint">OAuth follows the provider authorization flow; BYOK encrypts the supplied credential server-side.</p></section>
    </div>
    <section className="card-pad"><div className="flex items-center justify-between"><div><h2 className="text-sm font-semibold">Connected accounts</h2><FreshnessNote label="Connection status" detail="from integrations API" /></div><span className="text-xs text-ink-faint">{connections.length} connected</span></div><div className="mt-3 space-y-2">{connections.length ? connections.map((c) => <div key={c.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-surface-3/50 p-3"><div><div className="font-semibold text-sm text-ink">{c.connection_name || catalog.find((x) => x.provider === c.provider)?.name || c.provider}</div><div className="text-xs text-ink-muted">{catalog.find((x) => x.provider === c.provider)?.name || c.provider} · {c.auth_mode || "platform"} · {c.status || "status unavailable"}</div></div><button aria-label={`Test ${c.connection_name || c.provider}`} className="btn-secondary" disabled={busy} onClick={() => test(c.id)}>Test connection</button></div>) : <EmptyState title="No connected accounts" hint="Choose a provider above to connect LeadX to your revenue stack." action={<UnavailableNote label="Connection health" /> } />}</div></section>
  </div>;
}
