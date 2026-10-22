"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getApiBases } from "@/lib/config";
import { getSessionFast, signOut } from "@/lib/auth";
import { getTenantId, getTenantUserId, setTenant, clearTenant } from "@/lib/tenant";

const BOOTSTRAP_TIMEOUT_MS = 3500;

async function fetchWithTimeout(url: string, init: RequestInit, ms: number) {
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), ms);
  try {
    return await fetch(url, { ...init, signal: ctrl.signal, cache: "no-store" });
  } finally {
    window.clearTimeout(timer);
  }
}

export function SessionGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState("");
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    async function resolve() {
      try {
        const session = await Promise.race([
          getSessionFast(),
          new Promise<never>((_, reject) => window.setTimeout(() => reject(new Error("Session restore timed out. Please retry.")), 3500)),
        ]);
        if (!session) {
          router.replace("/login");
          return;
        }
        // LeadX uses its own OTP verification flow. Do not allow an unconfirmed
        // account into a tenant workspace, even when Supabase returned a session.
        if (!session.user.email_confirmed_at) {
          const email = encodeURIComponent(session.user.email || "");
          router.replace(`/verify-email?email=${email}`);
          return;
        }

        // Tenant resolution is intentionally done once per browser session.
        // Previously this ran on every pathname change, causing slow navigation.
        const cachedTenant = getTenantId();
        const cachedTenantUser = getTenantUserId();
        if (cachedTenant && cachedTenantUser === session.user.id) {
          if (!cancelled) setReady(true);
          return;
        }
        if (cachedTenant) clearTenant();

        // The backend session is held in HttpOnly cookies; no browser-readable
        // bearer token is attached to CRM requests.

    // Tenant identity is server-authoritative. Do not trust user_metadata or
    // app_metadata as an authorization shortcut; resolve membership through
    // /crm/me so workspace chrome cannot drift from backend access.
        const bases = getApiBases();

        // Codespaces can expose a stale same-origin /backend proxy while the
        // forwarded :8000 API is healthy. Race both targets instead of waiting
        // 3.5s on one and then trying the other (the old path caused ~6-7s login).
        const attempts = bases.map(async (base) => {
          const res = await fetchWithTimeout(`${base}/crm/me`, {
            credentials: "include", headers: { "Content-Type": "application/json" },
          }, BOOTSTRAP_TIMEOUT_MS);
          const raw = await res.text();
          let body: any = {};
          try { body = raw ? JSON.parse(raw) : {}; } catch { body = { raw }; }
          if (res.ok && body.tenant_id) {
            return { base, body };
          }
          const detail = body.detail || body.message || body.error || raw || res.statusText;
          throw new Error(`HTTP ${res.status}: ${detail}`);
        });

        let resolved: { base: string; body: any } | null = null;
        let lastError = "";
        try {
          resolved = await Promise.any(attempts);
        } catch (aggregate: any) {
          const errors = Array.isArray(aggregate?.errors) ? aggregate.errors : [];
          lastError = errors.map((e: any) => e?.message || String(e)).filter(Boolean).join(" | ");
        }

        if (resolved?.body?.tenant_id) {
          setTenant(resolved.body.tenant_id, resolved.body.tenant_name || "Workspace", session.user.id);
          window.dispatchEvent(new Event("leadx:tenant-ready"));
          if (!cancelled) setReady(true);
          return;
        }

        if (!cancelled) {
          setError(lastError.includes("403") || /not a member/i.test(lastError)
            ? "Your login is valid, but it is not linked to a client workspace."
            : "We couldn't connect your client workspace.");
          setDetail("The workspace service did not respond. Please retry; your credentials are not lost.");
          clearTenant();
        }
      } catch (e: any) {
        if (!cancelled) {
          setError(e?.message || "Unable to restore your session.");
          setDetail("The workspace service did not respond in time. Retry once; your credentials are not lost.");
        }
      }
    }
    resolve();
    return () => { cancelled = true; };
  }, [router, retryKey]);

  if (error) return (
    <div className="auth-shell">
      <div className="auth-card max-w-lg text-center">
        <span className="auth-logo">LX</span>
        <div className="mt-5 text-xs font-bold uppercase tracking-[.16em] text-brand-600">Workspace connection</div>
        <h1 className="mt-2 text-2xl font-bold text-ink">Almost there</h1>
        <p className="mt-2 text-sm text-ink-muted">{error}</p>
        <p className="mt-3 text-xs leading-5 text-ink-faint">{detail}</p>
        <div className="mt-6 flex justify-center gap-2">
          <button aria-label="Action" className="btn btn-secondary" onClick={() => { setError(""); setDetail(""); setReady(false); setRetryKey((v) => v + 1); }}>Retry</button>
          <button aria-label="Action" className="btn-primary" onClick={async () => { clearTenant(); await signOut().catch(() => {}); router.replace("/login"); }}>Sign out</button>
        </div>
      </div>
    </div>
  );

  if (!ready) return (
    <div className="auth-shell">
      <div className="auth-card max-w-sm text-center">
        <span className="auth-logo">LX</span>
        <div className="mt-5 h-1.5 overflow-hidden rounded-full bg-surface-3"><div className="h-full w-1/2 animate-pulse rounded-full bg-brand-500" /></div>
        <p className="mt-4 text-sm font-semibold text-ink">Opening your workspace</p>
        <p className="mt-1 text-xs text-ink-muted">Securing your session…</p>
      </div>
    </div>
  );

  return <>{children}</>;
}
