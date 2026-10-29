"use client";

import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { signUpWithPassword, signInWithPassword } from "@/lib/auth";
import { acceptInvite } from "@/lib/api";
import { getApiBase } from "@/lib/config";

export default function AcceptInvitePage() {
  const params = useSearchParams();
  const router = useRouter();
  const token = params.get("token") || "";
  const [email, setEmail] = useState(params.get("email") || "");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [mode, setMode] = useState<"new" | "existing">(params.get("verified") === "1" ? "existing" : "new");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!token) {
      setError("Missing invite token — use the link from your invite email.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      if (mode === "new") {
        const signup: any = await signUpWithPassword(email.trim(), password);
        const authUserId = signup?.user?.id;
        const verifyRes = await fetch(`${getApiBase()}/auth/email/send-code`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: email.trim(), auth_user_id: authUserId }),
        });
        const verifyData = await verifyRes.json().catch(() => ({}));
        if (!verifyRes.ok) throw new Error(verifyData.detail || "Could not send verification code");
        router.replace(`/verify-email?email=${encodeURIComponent(email.trim())}&invite=${encodeURIComponent(token)}`);
        return;
      } else {
        await signInWithPassword(email.trim(), password);
      }
      const joined: any = await acceptInvite(token, fullName.trim() || undefined);
      if (joined?.tenant_id) {
        try {
          const { setTenant } = await import("@/lib/tenant");
          setTenant(joined.tenant_id, "Workspace");
        } catch { /* ignore */ }
      }
      router.push("/dashboard");
    } catch (err: any) {
      setError(err.message || "Could not accept invite");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-shell">
      <div className="relative z-10 flex w-full max-w-[860px] items-stretch justify-center">
        <AuthAside />
        <form
          onSubmit={submit}
          className="auth-card space-y-5 lg:rounded-l-none lg:border-l-0"
        >
          <div>
            <span className="auth-logo">LX</span>
            <h1 className="auth-title">Accept invite</h1>
            <p className="auth-copy">
              Create an account (set your password) or sign in, then you join this workspace with the role you were invited with.
            </p>
            {!token && (
              <div className="mt-3 notice notice-danger" role="alert">
                No invite token in the URL — open this page from your invite link.
              </div>
            )}
          </div>
          <div className="tabs grid w-full grid-cols-2" role="group" aria-label="Account type">
            <button
              type="button"
              aria-pressed={mode === "new"}
              onClick={() => setMode("new")}
              className={mode === "new" ? "tab-active" : "tab"}
            >
              I'm new
            </button>
            <button
              type="button"
              aria-pressed={mode === "existing"}
              onClick={() => setMode("existing")}
              className={mode === "existing" ? "tab-active" : "tab"}
            >
              I already have an account
            </button>
          </div>
          {mode === "new" && (
            <div>
              <label htmlFor="invite-full-name" className="field-label">Full name</label>
              <input
                id="invite-full-name"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                placeholder="Full name"
                autoComplete="name"
                className="input w-full"
              />
            </div>
          )}
          <div>
            <label htmlFor="invite-email" className="field-label">Email</label>
            <input
              id="invite-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="Email"
              autoComplete="email"
              className="input w-full"
              required
            />
          </div>
          <div>
            <label htmlFor="invite-password" className="field-label">Password</label>
            <input
              id="invite-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Password"
              autoComplete={mode === "new" ? "new-password" : "current-password"}
              className="input w-full"
              required
            />
            {mode === "new" ? <p className="field-hint">We&apos;ll email a verification code before you join.</p> : null}
          </div>
          {error && <p role="alert" className="notice notice-danger">{error}</p>}
          <button
            type="submit"
            disabled={busy || !token}
            className="btn-primary w-full"
          >
            {busy ? "Joining…" : "Join workspace"}
          </button>
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
        <p className="font-display text-[1.375rem] font-semibold leading-snug tracking-[-0.02em]">You&apos;ve been invited to a LeadX workspace.</p>
        <p className="mt-4 text-[0.8125rem] leading-6 text-brand-100">Your role was set by the workspace admin. Once you join, you&apos;ll see the same leads, pipeline and reports as the rest of your team.</p>
      </div>
      <p className="text-xs text-brand-200">Growth intelligence for agencies and revenue teams.</p>
    </aside>
  );
}
