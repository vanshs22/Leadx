"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { admin } from "@/lib/admin";
import { signInWithPassword, signOut } from "@/lib/auth";

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

  return <div className="auth-shell"><div className="auth-card">
    <span className="auth-logo">LX</span><div className="eyebrow mt-4 text-brand-700 dark:text-brand-300">LeadX · Control plane</div><h1 className="auth-title">Admin access</h1><p className="mt-2 text-sm text-ink-muted">Sign in with a Supabase user that is enabled in <code>platform_admins</code>. No service credential is stored in the browser.</p>
    <form onSubmit={secureLogin} className="mt-5 space-y-3"><input value={email} onChange={e=>setEmail(e.target.value)} type="email" placeholder="Admin email" className="w-full rounded-lg border border-line bg-surface-2 px-3 py-2.5 text-sm" autoFocus/><input value={password} onChange={e=>setPassword(e.target.value)} type="password" placeholder="Password" className="w-full rounded-lg border border-line bg-surface-2 px-3 py-2.5 text-sm"/><button aria-label="Action" disabled={busy||!email||!password} className="btn-primary w-full py-2.5">{busy?'Signing in…':'Sign in securely'}</button></form>
    {error&&<div className="mt-4 rounded-xl border border-rust-300 bg-rust-50 p-3 text-sm text-rust-700 dark:bg-rust-500/10 dark:text-rust-300">{error}</div>}
  </div></div>;
}
