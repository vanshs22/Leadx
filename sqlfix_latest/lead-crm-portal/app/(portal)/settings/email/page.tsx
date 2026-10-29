"use client";

import { useLiveLoader } from "@/lib/live";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { getTenantId } from "@/lib/tenant";
import { getApiBase } from "@/lib/config";
import { getSupabase } from "@/lib/supabaseClient";
import { Badge, PageHeader, Notice, Panel, StatusBadge } from "@/components/ui/Primitives";
import { Icon } from "@/components/ui/Icon";

type DnsRec = { type?: string; name?: string; value?: string };
type Settings = {
  domain?: string;
  from_email?: string;
  from_name?: string;
  domain_verified?: boolean;
  dkim_status?: string;
  dns_records?: DnsRec[];
};

type Phase = "loading" | "no_aws" | "not_connected" | "pending_dns" | "ready";

async function authHeaders(): Promise<Record<string, string>> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  try {
    const sb = getSupabase();
    const { data } = await sb.auth.getSession();
    const token = data.session?.access_token;
    if (token) h["Authorization"] = `Bearer ${token}`;
  } catch {
    /* optional in dev */
  }
  const tid = getTenantId();
  if (tid) h["X-Tenant-Id"] = tid;
  return h;
}

export default function EmailSettingsPage() {
  const [domain, setDomain] = useState("");
  const [fromEmail, setFromEmail] = useState("");
  const [fromName, setFromName] = useState("");
  const [settings, setSettings] = useState<Settings | null>(null);
  const [dns, setDns] = useState<DnsRec[]>([]);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [sesReady, setSesReady] = useState<boolean | null>(null);
  const [phase, setPhase] = useState<Phase>("loading");
  const [lastChecked, setLastChecked] = useState<string | null>(null);
  const [pollingExpired, setPollingExpired] = useState(false);

  const tid = typeof window !== "undefined" ? getTenantId() : "";

  const derivePhase = useCallback(
    (sesAccount: boolean | null, s: Settings | null): Phase => {
      if (sesAccount === false) return "no_aws";
      if (!s?.domain) return "not_connected";
      if (s.domain_verified) return "ready";
      return "pending_dns";
    },
    []
  );

  const load = useCallback(async () => {
    if (!tid) {
      setPhase("not_connected");
      return;
    }
    try {
      const r = await fetch(`${getApiBase()}/crm/${tid}/email-settings`, {
        headers: await authHeaders(),
        credentials: "include",
      });
      const j = await r.json();
      const accountOk = !!j.ses_account_ready;
      setSesReady(accountOk);
      if (j.settings) {
        setSettings(j.settings);
        setDomain(j.settings.domain || "");
        setFromEmail(j.settings.from_email || "");
        setFromName(j.settings.from_name || "");
        setDns(j.settings.dns_records || []);
        setPhase(derivePhase(accountOk, j.settings));
      } else {
        setSettings(null);
        setPhase(derivePhase(accountOk, null));
      }
    } catch (e: any) {
      setErr(e?.message || "load failed");
      setPhase("not_connected");
    }
  }, [tid, derivePhase]);

  useEffect(() => {
    load();
  }, [load]);

  const pollDns = useCallback(async () => {
    if (phase !== "pending_dns" || !tid) return;
    try {
      const r = await fetch(`${getApiBase()}/crm/${tid}/email-settings/check`, { method: "POST", headers: await authHeaders(), credentials: "include" });
      const j = await r.json().catch(() => ({}));
      setLastChecked(new Date().toLocaleTimeString());
      if (r.ok && j.verified) { setMsg("Domain verified — you are ready to bulk send."); await load(); }
    } catch { /* bounded polling is best effort; manual check remains available */ }
  }, [phase, tid, load]);

  useLiveLoader(pollDns, phase === "pending_dns" ? 20000 : 0, [phase, tid], { onDeadline: () => setPollingExpired(true) });

  async function connect() {
    setLoading(true);
    setErr("");
    setMsg("");
    try {
      const r = await fetch(`${getApiBase()}/crm/${tid}/email-settings/domain`, {
        method: "POST",
        headers: await authHeaders(),
        credentials: "include",
        body: JSON.stringify({ domain, from_email: fromEmail, from_name: fromName }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || j.error || r.statusText);
      setDns(j.dns_records || []);
      setMsg(
        j.verified
          ? "Domain already verified — ready to bulk send."
          : "Domain registered on platform SES. Add the DNS records below, then wait or click Check status."
      );
      await load();
    } catch (e: any) {
      setErr(typeof e?.message === "string" ? e.message : "connect failed");
    } finally {
      setLoading(false);
    }
  }

  async function check() {
    setLoading(true);
    setErr("");
    setMsg("");
    try {
      const r = await fetch(`${getApiBase()}/crm/${tid}/email-settings/check`, {
        method: "POST",
        headers: await authHeaders(),
        credentials: "include",
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || j.error || r.statusText);
      setLastChecked(new Date().toLocaleTimeString());
      setMsg(
        j.verified
          ? "Verified — you can bulk send from Leads now."
          : "Not verified yet — DNS may still be propagating (often 15 minutes to 72 hours)."
      );
      if (j.dns_records?.length) setDns(j.dns_records);
      await load();
    } catch (e: any) {
      setErr(typeof e?.message === "string" ? e.message : "check failed");
    } finally {
      setLoading(false);
    }
  }

  const statusBanner = {
    loading: {
      cls: "notice-neutral",
      title: "Checking email setup…",
      body: "Loading your domain status.",
    },
    no_aws: {
      cls: "notice-warning",
      title: "Platform email not ready",
      body: "SES is not ready on the server. If keys are in .env, restart the API. Wrong/deactivated keys show as invalid credentials — create a new IAM access key.",
    },
    not_connected: {
      cls: "notice-neutral",
      title: "Domain not connected",
      body: "Enter your domain and From address, then click Connect domain. You will get DNS records to add at your registrar.",
    },
    pending_dns: {
      cls: "notice-warning",
      title: "Waiting for DNS",
      body:
        "Domain is registered. Add the CNAME records below at your DNS host, then Check status. This page auto-checks every 20 seconds" +
        (lastChecked ? ` (last check ${lastChecked}).` : "."),
    },
    ready: {
      cls: "notice-success",
      title: "Ready to bulk send",
      body: `Verified as ${settings?.from_email || settings?.domain || "your domain"}. Open Leads, select contacts, and send — mail goes From your address via platform SES.`,
    },
  }[phase];

  const setupSteps: Array<{ label: string; state: "done" | "failed" | "pending" }> = [
    { label: "Platform SES available", state: sesReady ? "done" : sesReady === false ? "failed" : "pending" },
    { label: `Domain connected${settings?.domain ? ` (${settings.domain})` : ""}`, state: settings?.domain ? "done" : "pending" },
    { label: "DNS records issued", state: dns.length || settings?.dns_records?.length ? "done" : "pending" },
    { label: "Domain verified — bulk send unlocked", state: settings?.domain_verified ? "done" : "pending" },
  ];
  const phaseStatus = phase === "ready" ? "verified" : phase === "pending_dns" ? "pending" : phase === "no_aws" ? "blocked" : phase === "loading" ? "processing" : "draft";
  const phaseLabel = phase === "ready" ? "Verified" : phase === "pending_dns" ? "Awaiting DNS" : phase === "no_aws" ? "SES unavailable" : phase === "loading" ? "Checking" : "Not connected";

  return (
    <div className="page max-w-5xl">
      <PageHeader eyebrow="Workspace settings" title="Email & domain" description="Link your domain for bulk email. You only manage DNS; sending runs on the platform Amazon SES account." meta={<><StatusBadge status={phaseStatus} label={phaseLabel} />{lastChecked ? <span className="tabular-nums">Last check {lastChecked}</span> : null}</>} />

      {/* Live status */}
      {pollingExpired ? <Notice tone="warning" title="Live verification paused">Refresh or check status manually to start a new bounded verification window.</Notice> : null}
      <div className={`notice ${statusBanner.cls} flex-col !items-start !justify-start gap-0`} role="status" aria-live="polite">
        <div className="font-semibold">{statusBanner.title}</div>
        <p className="mt-0.5 opacity-90">{statusBanner.body}</p>
        {phase === "ready" && (
          <Link href="/leads" className="mt-2 inline-flex items-center gap-1 text-[0.8125rem] font-semibold underline-offset-2 hover:underline">
            Go to Leads to bulk send<Icon name="arrow" size={13} aria-hidden="true" />
          </Link>
        )}
        {phase === "pending_dns" && (
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" className="btn-primary btn-sm" disabled={loading} onClick={() => { setPollingExpired(false); void check(); }}>
              Check status now
            </button>
          </div>
        )}
      </div>

      <div className="grid items-start gap-4 lg:grid-cols-3">
        <Panel
          title="Sending domain"
          description="The domain and From identity your bulk email uses."
          className="lg:col-span-2"
          footer={<>
            <span className="min-w-0">{msg ? null : "You'll get DNS records to add at your registrar."}</span>
            <div className="flex shrink-0 flex-wrap gap-2">
              <button type="button" className="btn-ghost" disabled={loading || !domain} onClick={() => { setPollingExpired(false); void check(); }}>
                Check status
              </button>
              <button type="button" className="btn-primary" disabled={loading || !domain} onClick={connect}>
                {loading ? "Working…" : "Connect domain"}
              </button>
            </div>
          </>}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <label htmlFor="email-domain" className="field-label">Domain</label>
              <input id="email-domain" className="input" placeholder="youragency.com" value={domain} onChange={(e) => setDomain(e.target.value)} />
              <p className="field-hint">The root domain you send from, without https://.</p>
            </div>
            <div>
              <label htmlFor="email-from-email" className="field-label">From email</label>
              <input id="email-from-email" className="input" placeholder="leads@youragency.com" value={fromEmail} onChange={(e) => setFromEmail(e.target.value)} />
            </div>
            <div>
              <label htmlFor="email-from-name" className="field-label">From name</label>
              <input id="email-from-name" className="input" placeholder="Your Agency" value={fromName} onChange={(e) => setFromName(e.target.value)} />
            </div>
          </div>
          {msg || err ? (
            <div className="mt-4 space-y-2">
              {msg && <Notice tone="success">{msg}</Notice>}
              {err && <Notice tone="danger" title="Email setup unavailable">{String(err)}</Notice>}
            </div>
          ) : null}
        </Panel>

        {/* Step checklist */}
        <Panel title="Setup progress" description="Bulk send unlocks after verification.">
          <ol className="space-y-3 text-[0.8125rem]">
            {setupSteps.map((s, i) => (
              <li key={i} className="flex items-start gap-2.5">
                <span className={`mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold tabular-nums ${s.state === "done" ? "bg-emerald-600 text-white dark:bg-emerald-400 dark:text-emerald-950" : s.state === "failed" ? "bg-rust-600 text-white dark:bg-rust-400 dark:text-rust-950" : "border border-line-strong text-ink-faint"}`} aria-hidden="true">
                  {s.state === "done" ? <Icon name="check" size={12} /> : s.state === "failed" ? <Icon name="close" size={12} /> : i + 1}
                </span>
                <span className={s.state === "done" ? "text-ink" : "text-ink-muted"}>{s.label}<span className="sr-only">{s.state === "done" ? " — complete" : s.state === "failed" ? " — failed" : " — pending"}</span></span>
              </li>
            ))}
          </ol>
        </Panel>
      </div>

      {dns.length > 0 && (
        <Panel title="DNS records to add" description="Add these CNAMEs at Cloudflare, GoDaddy, Namecheap, etc. Status above updates when verification succeeds." bodyClassName="p-0">
          <div className="overflow-x-auto">
            <table className="data-table">
              <caption className="sr-only">DNS records to add</caption>
              <thead>
                <tr>
                  <th scope="col" className="w-[90px]">Type</th>
                  <th scope="col">Name / Host</th>
                  <th scope="col">Value / Target</th>
                </tr>
              </thead>
              <tbody>
                {dns.map((rec, i) => (
                  <tr key={i}>
                    <td><Badge tone="neutral" className="font-mono">{rec.type || "CNAME"}</Badge></td>
                    <td className="break-all font-mono text-xs">{rec.name}</td>
                    <td className="break-all font-mono text-xs">{rec.value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </div>
  );
}
