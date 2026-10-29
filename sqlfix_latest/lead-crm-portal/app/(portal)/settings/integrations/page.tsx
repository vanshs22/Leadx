"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useCrmChange } from "@/lib/crmRealtime";
import { useLiveLoader } from "@/lib/live";
import { crmIntegrations } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/States";
import { FreshnessNote, UnavailableNote } from "@/components/ui/SurfaceMeta";
import { Badge, PageHeader, Notice, Panel, StatusBadge } from "@/components/ui/Primitives";
import { Icon } from "@/components/ui/Icon";

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
  const [pollingExpired, setPollingExpired] = useState(false);
  const [pollingGeneration, setPollingGeneration] = useState(0);
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
  useLiveLoader(() => load(), 30_000, [pollingGeneration], { runImmediately: false, onDeadline: () => setPollingExpired(true) });
  useCrmChange(() => { void load(); }, { tables: ["integrations", "tenant_integrations"] });
  const refresh = () => { setPollingExpired(false); setPollingGeneration((value) => value + 1); void load(); };
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

  const header = (actions?: ReactNode) => <PageHeader eyebrow="LeadX Connect" title="Integrations" description="Connect managed OAuth or your own credentials. Credentials stay server-side and are never rendered back to the browser." actions={actions} meta={catalog.length ? <><FreshnessNote label="Connector catalog" detail={`${catalog.length} providers available`} /><span className="tabular-nums">{connections.length} connected</span></> : null} />;
  if (loading) return <div className="page">{header()}<div className="card"><LoadingRows rows={5} cols={3} /></div></div>;
  if (error) return <div className="page">{header()}<ErrorState error={error} onRetry={load} /></div>;

  const connectedProviders = new Set(connections.map((c) => c.provider));
  return <div className="page max-w-7xl">
    {header(<button className="btn-secondary" onClick={refresh} disabled={loading}><Icon name="refresh" size={14} aria-hidden="true" />Refresh</button>)}
    {pollingExpired ? <Notice tone="warning" title="Live integration updates paused">Refresh to start a new bounded live-update window.</Notice> : null}
    {msg && <Notice tone={msg.includes("failed") || msg.includes("Failed") ? "danger" : "neutral"}>{msg}</Notice>}
    <div className="grid items-start gap-4 lg:grid-cols-3">
      <Panel
        title="Connector catalog"
        description="Pick a provider to configure it on the right."
        className="lg:col-span-2"
        actions={<Badge tone="brand"><span className="tabular-nums">{catalog.length}</span> connectors</Badge>}
      >
        <label htmlFor="integration-search" className="sr-only">Search connectors</label>
        <div className="relative">
          <Icon name="search" size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint" aria-hidden="true" />
          <input id="integration-search" type="search" className="input pl-8" placeholder="Search connectors by purpose or provider…" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        {filtered.length ? (
          <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {filtered.map((p) => {
              const isSelected = provider === p.provider, isConnected = connectedProviders.has(p.provider);
              return (
                <button aria-label={`Select ${p.name}`} aria-pressed={isSelected} key={p.provider} onClick={() => select(p)} className={`flex flex-col rounded-lg border p-3.5 text-left transition-colors ${isSelected ? "border-brand-500 bg-brand-50 ring-1 ring-brand-500/30 dark:border-brand-300 dark:bg-brand-500/10" : "border-line bg-surface-2 hover:border-line-strong hover:bg-surface-3/50"}`}>
                  <div className="flex w-full items-start justify-between gap-2">
                    <span className="flex min-w-0 items-center gap-2"><span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-line bg-surface-3 text-[11px] font-semibold uppercase text-ink-muted" aria-hidden="true">{String(p.name || p.provider).slice(0, 2)}</span><span className="truncate text-[0.8125rem] font-semibold text-ink">{p.name}</span></span>
                    <StatusBadge status={isConnected ? "connected" : "cancelled"} label={isConnected ? "Connected" : "Not connected"} />
                  </div>
                  <div className="mt-2 line-clamp-2 text-xs leading-5 text-ink-muted">{p.purpose || "Provider purpose unavailable"}</div>
                  <div className="mt-auto pt-2.5 text-[11px] text-ink-faint"><span className="capitalize">{p.category || "Category unavailable"}</span> · {p.auth || "Auth unavailable"}</div>
                </button>
              );
            })}
          </div>
        ) : <EmptyState title="No connectors match" hint="Try a broader provider or purpose search." />}
      </Panel>
      <Panel
        title={<>Connect {selected?.name || provider}</>}
        description="OAuth follows the provider authorization flow; BYOK encrypts the supplied credential server-side."
        className="lg:sticky lg:top-20"
        footer={<><span>{mode === "platform" ? "You'll be redirected to the provider." : "Credentials are not shown again."}</span><button aria-label="Connect selected provider" className="btn-primary shrink-0" disabled={busy} onClick={connect}>{busy ? "Connecting…" : mode === "platform" ? `Connect ${selected?.name || provider}` : "Save encrypted BYOK connection"}</button></>}
      >
        <div className="space-y-4">
          <div>
            <span className="field-label">Authentication</span>
            <div className="tabs grid w-full grid-cols-2" role="group" aria-label="Authentication mode">
              <button aria-label="Use LeadX OAuth" aria-pressed={mode === "platform"} onClick={() => setMode("platform")} className={mode === "platform" ? "tab-active" : "tab"}>LeadX OAuth</button>
              <button aria-label="Use BYOK" aria-pressed={mode === "byok"} onClick={() => { setMode("byok"); setConfig(BYOK_FIELDS[provider] || {}); }} className={mode === "byok" ? "tab-active" : "tab"}>My BYOK</button>
            </div>
          </div>
          {mode === "byok" && <div><label htmlFor="integration-connection-name" className="field-label">Connection name</label><input id="integration-connection-name" className="input" placeholder="e.g. Sales Gmail" value={name} onChange={(e) => setName(e.target.value)} /><p className="field-hint">Optional label to tell multiple accounts apart.</p></div>}
          {mode === "byok" && Object.entries(config).map(([k, v]) => <div key={k}><label htmlFor={`integration-field-${k}`} className="field-label capitalize">{k.replace(/_/g, " ")}</label><input id={`integration-field-${k}`} className={`input ${k.includes("token") || k.includes("key") || k.includes("secret") ? "font-mono" : ""}`} type={k.includes("token") || k.includes("key") || k.includes("secret") ? "password" : "text"} value={v} onChange={(e) => setConfig({ ...config, [k]: e.target.value })} /></div>)}
          {mode === "platform" ? <p className="text-[0.8125rem] leading-6 text-ink-muted">LeadX manages the OAuth app. You approve access on the provider&apos;s consent screen.</p> : null}
        </div>
      </Panel>
    </div>
    <Panel title="Connected accounts" description="Health is checked on demand from the integrations API." actions={<FreshnessNote label="Connection status" detail="from integrations API" />} bodyClassName={connections.length ? "p-0" : "panel-body"}>
      {connections.length ? (
        <div className="overflow-x-auto">
          <table className="data-table">
            <caption className="sr-only">Connected accounts</caption>
            <thead><tr><th scope="col">Account</th><th scope="col">Provider</th><th scope="col">Auth</th><th scope="col">Status</th><th scope="col" className="!text-right"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{connections.map((c) => <tr key={c.id}><td className="font-medium text-ink">{c.connection_name || catalog.find((x) => x.provider === c.provider)?.name || c.provider}</td><td className="text-ink-muted">{catalog.find((x) => x.provider === c.provider)?.name || c.provider}</td><td><Badge tone="neutral" className="uppercase">{c.auth_mode || "platform"}</Badge></td><td>{c.status ? <StatusBadge status={c.status} /> : <span className="text-xs text-ink-faint">status unavailable</span>}</td><td className="text-right"><button aria-label={`Test ${c.connection_name || c.provider}`} className="btn-secondary btn-sm" disabled={busy} onClick={() => test(c.id)}>Test connection</button></td></tr>)}</tbody>
          </table>
        </div>
      ) : <EmptyState title="No connected accounts" hint="Choose a provider above to connect LeadX to your revenue stack." action={<UnavailableNote label="Connection health" /> } />}
    </Panel>
  </div>;
}
