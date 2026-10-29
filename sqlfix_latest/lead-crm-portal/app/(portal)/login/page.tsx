"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { signInWithPassword } from "@/lib/auth";
import { isLocalAuthProfile } from "@/lib/config";
import { Icon } from "@/components/ui/Icon";

export default function ClientLoginPage() {
  const router = useRouter();
  // Warm the dashboard bundle while the user enters credentials.
  useEffect(() => { try { router.prefetch("/dashboard"); } catch {} }, [router]);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await signInWithPassword(email.trim(), password);
      // SessionGate performs the single workspace bootstrap; avoid a second
      // server refresh here, which used to add another round-trip to login.
      const next = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("next") : null;
      router.replace(next || "/dashboard");
    } catch (err: any) {
      setError(err?.message || "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-shell">
      <div className="relative z-10 flex w-full max-w-[860px] items-stretch justify-center">
        <AuthAside />
        <form
          onSubmit={onSubmit}
          className="auth-card space-y-5 lg:rounded-l-none lg:border-l-0"
        >
          <div>
            <span className="auth-logo">LX</span>
            <h1 className="auth-title">Welcome back</h1>
            <p className="auth-copy">
              {isLocalAuthProfile() ? "Local validation mode: use local@example.test with the synthetic password." : "Sign in with the email and password your agency admin gave you."}
            </p>
          </div>
          <div>
            <label htmlFor="login-email" className="field-label">Email</label>
            <input
              id="login-email"
              type="email"
              required
              autoComplete="username"
              className="input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="login-password" className="field-label">Password</label>
            <input
              id="login-password"
              type="password"
              required
              autoComplete="current-password"
              className="input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {error ? <p role="alert" className="notice notice-danger">{error}</p> : null}
          <button
            type="submit"
            disabled={busy}
            className="btn-primary w-full"
          >
            {busy ? "Securing session…" : "Continue to workspace"}
          </button>
          <div className="soft-divider" />
          <p className="text-center text-xs text-ink-faint">
            Operator?{" "}
            <a href="/admin/login" className="link">
              Admin login
            </a>
          </p>
        </form>
      </div>
    </div>
  );
}

/** Restrained product statement shown beside the card on large screens. */
function AuthAside() {
  return (
    <aside className="hidden w-[400px] shrink-0 flex-col justify-between rounded-l-xl border border-r-0 border-brand-800 bg-brand-900 p-8 text-white lg:flex" aria-label="About LeadX">
      <div className="flex items-center gap-2.5"><span className="brand-mark ring-1 ring-white/15">LX</span><span className="font-display text-[15px] font-semibold tracking-[-0.02em]">LeadX</span></div>
      <div>
        <p className="font-display text-[1.375rem] font-semibold leading-snug tracking-[-0.02em]">Qualified pipeline, with the evidence behind every lead.</p>
        <ul className="mt-6 space-y-3 text-[0.8125rem] text-brand-100">
          {["Verified contacts and tiered scoring", "Live pipeline and outreach in one workspace", "Workspace-scoped access for every teammate"].map((line) => (
            <li key={line} className="flex items-start gap-2.5"><Icon name="check" size={14} className="mt-0.5 shrink-0 text-brand-200" aria-hidden="true" />{line}</li>
          ))}
        </ul>
      </div>
      <p className="text-xs text-brand-200">Growth intelligence for agencies and revenue teams.</p>
    </aside>
  );
}
