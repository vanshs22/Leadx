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
    <div className="flex min-h-screen items-center justify-center p-6">
      <form
        onSubmit={submit}
        className="w-full max-w-md space-y-4 rounded-2xl border border-line bg-panel p-8 shadow-lg"
      >
        <div>
          <h1 className="text-xl font-semibold text-ink">Accept invite</h1>
          <p className="mt-1 text-sm text-ink-muted">
            Create an account (set your password) or sign in, then you join this workspace with the role you were invited with.
          </p>
          {!token && (
            <p className="mt-2 text-sm text-rust-500">
              No invite token in the URL — open this page from your invite link.
            </p>
          )}
        </div>
        <div className="flex gap-2 text-xs">
          <button
            type="button"
            onClick={() => setMode("new")}
            className={mode === "new" ? "btn-primary" : "btn-secondary"}
          >
            I'm new
          </button>
          <button
            type="button"
            onClick={() => setMode("existing")}
            className={mode === "existing" ? "btn-primary" : "btn-secondary"}
          >
            I already have an account
          </button>
        </div>
        {mode === "new" && (
          <input
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            placeholder="Full name"
            className="input w-full"
          />
        )}
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Email"
          className="input w-full"
          required
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          className="input w-full"
          required
        />
        {error && <p className="text-sm text-rust-500">{error}</p>}
        <button
          type="submit"
          disabled={busy || !token}
          className="btn-primary w-full disabled:opacity-50"
        >
          {busy ? "Joining…" : "Join workspace"}
        </button>
      </form>
    </div>
  );
}
