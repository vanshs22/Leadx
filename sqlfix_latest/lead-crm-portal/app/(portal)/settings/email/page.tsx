"use client";

import { setVisibleInterval, clearVisibleInterval } from "../../../../lib/live";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { getTenantId } from "@/lib/tenant";
import { getApiBase } from "@/lib/config";
import { getSupabase } from "@/lib/supabaseClient";

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

  // Live poll while waiting on DNS (every 20s)
  useEffect(() => {
    if (phase !== "pending_dns" || !tid) return;
    const id = setVisibleInterval(async () => {
      try {
        const r = await fetch(`${getApiBase()}/crm/${tid}/email-settings/check`, {
          method: "POST",
          headers: await authHeaders(),
          credentials: "include",
        });
        const j = await r.json().catch(() => ({}));
        setLastChecked(new Date().toLocaleTimeString());
        if (r.ok && j.verified) {
          setMsg("Domain verified — you are ready to bulk send.");
          await load();
        }
      } catch {
        /* ignore poll errors */
      }
    }, 20000);
    return () => clearVisibleInterval(id);
  }, [phase, tid, load]);

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
      cls: "border-line bg-surface-2 text-ink",
      title: "Checking email setup…",
      body: "Loading your domain status.",
    },
    no_aws: {
      cls: "border-amber-500/40 bg-amber-500/10 text-amber-900 dark:text-amber-100",
      title: "Platform email not ready",
      body: "SES is not ready on the server. If keys are in .env, restart the API. Wrong/deactivated keys show as invalid credentials — create a new IAM access key.",
    },
    not_connected: {
      cls: "border-line bg-surface-2 text-ink",
      title: "Domain not connected",
      body: "Enter your domain and From address, then click Connect domain. You will get DNS records to add at your registrar.",
    },
    pending_dns: {
      cls: "border-amber-500/40 bg-amber-500/10 text-amber-900 dark:text-amber-100",
      title: "Waiting for DNS",
      body:
        "Domain is registered. Add the CNAME records below at your DNS host, then Check status. This page auto-checks every 20 seconds" +
        (lastChecked ? ` (last check ${lastChecked}).` : "."),
    },
    ready: {
      cls: "border-emerald-500/40 bg-emerald-500/10 text-emerald-900 dark:text-emerald-100",
      title: "Ready to bulk send",
      body: `Verified as ${settings?.from_email || settings?.domain || "your domain"}. Open Leads, select contacts, and send — mail goes From your address via platform SES.`,
    },
  }[phase];

  return (
    <div className="page max-w-2xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-ink">Email & domain</h1>
        <p className="text-sm text-mute mt-1">
          Link your domain for bulk email. You only manage DNS; sending runs on the platform Amazon SES account.
        </p>
      </div>

      {/* Live status */}
      <div className={`rounded-xl border px-4 py-3 text-sm ${statusBanner.cls}`}>
        <div className="font-semibold">{statusBanner.title}</div>
        <p className="mt-1 opacity-90">{statusBanner.body}</p>
        {phase === "ready" && (
          <Link href="/leads" className="mt-2 inline-block text-sm font-medium underline">
            Go to Leads to bulk send →
          </Link>
        )}
        {phase === "pending_dns" && (
          <div className="mt-2 flex flex-wrap gap-2">
            <button aria-label="Action" type="button" className="btn btn-primary text-xs" disabled={loading} onClick={check}>
              Check status now
            </button>
          </div>
        )}
      </div>

      {/* Step checklist */}
      <div className="card">
        <h2 className="text-sm font-medium text-ink mb-3">Setup progress</h2>
        <ol className="space-y-2 text-sm">
          <li className="flex gap-2">
            <span>{sesReady ? "✅" : sesReady === false ? "❌" : "…"}</span>
            <span>Platform SES available</span>
          </li>
          <li className="flex gap-2">
            <span>{settings?.domain ? "✅" : "○"}</span>
            <span>Domain connected {settings?.domain ? `(${settings.domain})` : ""}</span>
          </li>
          <li className="flex gap-2">
            <span>{dns.length || settings?.dns_records?.length ? "✅" : "○"}</span>
            <span>DNS records issued</span>
          </li>
          <li className="flex gap-2">
            <span>{settings?.domain_verified ? "✅" : "○"}</span>
            <span>Domain verified — bulk send unlocked</span>
          </li>
        </ol>
      </div>

      <div className="card space-y-3">
        <label className="block text-sm">
          Domain
          <input
            className="input mt-1 w-full"
            placeholder="youragency.com"
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
          />
        </label>
        <label className="block text-sm">
          From email
          <input
            className="input mt-1 w-full"
            placeholder="leads@youragency.com"
            value={fromEmail}
            onChange={(e) => setFromEmail(e.target.value)}
          />
        </label>
        <label className="block text-sm">
          From name
          <input
            className="input mt-1 w-full"
            placeholder="Your Agency"
            value={fromName}
            onChange={(e) => setFromName(e.target.value)}
          />
        </label>
        <div className="flex flex-wrap gap-2">
          <button aria-label="Action" type="button" className="btn btn-primary" disabled={loading || !domain} onClick={connect}>
            {loading ? "Working…" : "Connect domain"}
          </button>
          <button aria-label="Action" type="button" className="btn btn-ghost" disabled={loading || !domain} onClick={check}>
            Check status
          </button>
        </div>
        {msg && <p className="text-sm text-ink">{msg}</p>}
        {err && <p className="text-sm text-red-600">{String(err)}</p>}
      </div>

      {dns.length > 0 && (
        <div className="card overflow-x-auto">
          <h2 className="font-medium text-ink mb-2">DNS records to add</h2>
          <p className="text-xs text-mute mb-3">
            Add these CNAMEs at Cloudflare, GoDaddy, Namecheap, etc. Status above updates when verification succeeds.
          </p>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-mute border-b border-line">
                <th className="py-2 pr-2">Type</th>
                <th className="py-2 pr-2">Name / Host</th>
                <th className="py-2">Value / Target</th>
              </tr>
            </thead>
            <tbody>
              {dns.map((rec, i) => (
                <tr key={i} className="border-b border-line/50">
                  <td className="py-2 pr-2 font-mono text-xs">{rec.type || "CNAME"}</td>
                  <td className="py-2 pr-2 font-mono text-xs break-all">{rec.name}</td>
                  <td className="py-2 font-mono text-xs break-all">{rec.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
