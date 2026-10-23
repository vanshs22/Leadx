"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { signInWithPassword } from "@/lib/auth";

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
      <form
        onSubmit={onSubmit}
        className="auth-card space-y-4"
      >
        <div>
          <span className="auth-logo">LX</span>
          <div className="eyebrow mt-4 text-brand-700 dark:text-brand-300">LeadX · Growth intelligence</div>
          <h1 className="auth-title">Welcome back</h1>
          <p className="mt-1 text-sm text-ink-muted">
            Sign in with the email and password your agency admin gave you.
          </p>
        </div>
        <label className="block text-xs text-ink-faint">
          Email
          <input
            type="email"
            required
            autoComplete="username"
            className="input mt-1"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label className="block text-xs text-ink-faint">
          Password
          <input
            type="password"
            required
            autoComplete="current-password"
            className="input mt-1"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error ? <p className="text-sm text-rust-400">{error}</p> : null}
        <button
          type="submit"
          disabled={busy}
          className="btn-primary w-full py-2.5"
        >
          {busy ? "Securing session…" : "Continue to workspace"}
        </button>
        <p className="text-center text-xs text-ink-faint">
          Operator?{" "}
          <a href="/admin/login" className="text-brand-500 hover:underline">
            Admin login
          </a>
        </p>
      </form>
    </div>
  );
}
