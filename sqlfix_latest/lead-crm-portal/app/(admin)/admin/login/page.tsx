"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { admin } from "@/lib/admin";
import { signInWithPassword, signOut } from "@/lib/auth";
import { isLocalAuthProfile } from "@/lib/config";
import { Icon } from "@/components/ui/Icon";

export default function AdminLoginPage() {
  const [email,setEmail]=useState(""); const [password,setPassword]=useState("");
  const [error,setError]=useState(""); const [busy,setBusy]=useState(false); const router=useRouter();

  async function secureLogin(e:React.FormEvent){
    e.preventDefault(); setBusy(true); setError("");
    try{
      const session=await signInWithPassword(email.trim(),password);
      if(!session) throw new Error("Could not create admin session");
      await admin.adminPing();
      router.push("/admin");
    }catch(err:any){await signOut().catch(()=>{});setError(err?.message||"Admin login failed");setBusy(false);}
  }

  return (
    <div className="auth-shell">
      <div className="auth-card border-t-2 border-t-signal-400 dark:border-t-signal-300">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <span className="auth-logo operator-brand-mark">LX</span>
            <span className="brand-wordmark">LeadX</span>
          </div>
          <span className="badge-accent"><Icon name="security" size={12} /> Operator</span>
        </div>
        <div className="eyebrow mt-7 text-signal-700 dark:text-signal-300">LeadX · Control plane</div>
        <h1 className="mt-1.5 font-display text-[1.375rem] font-semibold tracking-[-0.02em] text-ink">Admin access</h1>
        <p className="auth-copy">{isLocalAuthProfile() ? "Local validation mode: use local@example.test with the synthetic password." : <>Sign in with a Supabase user that is enabled in <code className="font-mono text-xs">platform_admins</code>. No service credential is stored in the browser.</>}</p>
        <form onSubmit={secureLogin} className="mt-6 space-y-4">
          <div>
            <label htmlFor="admin-email" className="field-label">Admin email</label>
            <input id="admin-email" value={email} onChange={e=>setEmail(e.target.value)} type="email" autoComplete="username" placeholder="Admin email" className="input h-10" autoFocus/>
          </div>
          <div>
            <label htmlFor="admin-password" className="field-label">Password</label>
            <input id="admin-password" value={password} onChange={e=>setPassword(e.target.value)} type="password" autoComplete="current-password" placeholder="Password" className="input h-10"/>
          </div>
          <button type="submit" disabled={busy||!email||!password} className="btn-primary w-full py-2.5">{busy?'Signing in…':'Sign in securely'}</button>
        </form>
        {error&&<div className="mt-4 notice notice-danger" role="alert">{error}</div>}
        <p className="mt-6 flex items-center gap-1.5 border-t border-line pt-4 text-xs text-ink-faint"><Icon name="info" size={13} className="shrink-0" />This sign-in is for LeadX operators. Workspace users sign in at <a href="/login" className="link">/login</a>.</p>
      </div>
    </div>
  );
}
