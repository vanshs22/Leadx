"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { billing } from "@/lib/api";
import { getSessionFast } from "@/lib/auth";

const PLANS: Record<string, { name: string; monthly: number; annual: number; leads: string }> = {
  starter: { name: "Starter", monthly: 49, annual: 39, leads: "500 qualified leads / month" },
  growth: { name: "Growth", monthly: 149, annual: 119, leads: "2,500 qualified leads / month" },
  agency: { name: "Agency", monthly: 399, annual: 319, leads: "8,000 qualified leads / month" },
};

export default function CheckoutPage() {
  const [planKey, setPlanKey] = useState("growth");
  const [interval, setBillingInterval] = useState<"monthly" | "annual">("annual");
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setPlanKey(params.get("plan") || "growth");
    setBillingInterval(params.get("interval") === "monthly" ? "monthly" : "annual");
    getSessionFast().then((s) => setSignedIn(!!s));
  }, []);

  const plan = PLANS[planKey] || PLANS.growth;
  const price = interval === "annual" ? plan.annual : plan.monthly;
  const yearlyTotal = interval === "annual" ? price * 12 : plan.monthly * 12;

  async function pay() {
    setBusy(true); setError("");
    try {
      const result = await billing.createCheckout(planKey, interval);
      if (result.redirect_url) window.location.href = result.redirect_url;
      else if (result.free) window.location.href = "/dashboard";
      else throw new Error("Checkout session was not created.");
    } catch (e: any) {
      setError(e?.message || "Could not start checkout");
      setBusy(false);
    }
  }

  if (new URLSearchParams(typeof window !== "undefined" ? window.location.search : "").get("success") === "1") {
    return <main className="min-h-screen bg-[#f7f8f6] px-5 py-16"><div className="mx-auto max-w-xl rounded-2xl border border-slate-200 bg-white p-8 text-center shadow-sm"><span className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-brand-50 text-xl text-brand-700">✓</span><h1 className="mt-5 text-2xl font-semibold tracking-tight">Payment received</h1><p className="mt-2 text-sm leading-6 text-slate-500">Your workspace is being upgraded. Stripe will confirm the subscription and LeadX will apply the new plan automatically.</p><Link href="/dashboard" className="mt-7 inline-flex rounded-xl bg-brand-700 px-5 py-3 text-sm font-semibold text-white hover:bg-brand-800">Open workspace</Link></div></main>;
  }

  return (
    <main className="min-h-screen bg-[#f7f8f6] text-slate-950">
      <header className="border-b border-slate-200 bg-white"><div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-4 sm:px-8"><Link href="/pricing" className="flex items-center gap-2.5"><span className="brand-mark h-9 w-9 rounded-[10px] text-[10px]">LX</span><span className="brand-wordmark">LeadX</span></Link><Link href="/pricing" className="text-sm font-semibold text-slate-600 hover:text-slate-950">Back to pricing</Link></div></header>
      <div className="mx-auto grid max-w-5xl gap-8 px-5 py-12 lg:grid-cols-[1fr_420px] sm:px-8">
        <section className="pt-4"><p className="eyebrow text-brand-700">Secure checkout</p><h1 className="mt-3 text-4xl font-semibold tracking-[-.045em]">Upgrade your LeadX workspace.</h1><p className="mt-4 max-w-xl text-base leading-7 text-slate-600">One subscription covers discovery, enrichment, research and the CRM workspace. Your final card details are handled by Stripe.</p><div className="mt-10 grid gap-3 sm:grid-cols-2"><div className="rounded-xl border border-slate-200 bg-white p-4"><p className="text-xs font-bold uppercase tracking-[.12em] text-slate-400">Included</p><p className="mt-2 text-sm font-semibold">{plan.leads}</p></div><div className="rounded-xl border border-slate-200 bg-white p-4"><p className="text-xs font-bold uppercase tracking-[.12em] text-slate-400">Billing</p><p className="mt-2 text-sm font-semibold">{interval === "annual" ? "Annual · 20% savings" : "Monthly · cancel anytime"}</p></div></div></section>
        <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-[0_12px_40px_rgba(15,23,42,.08)]">
          <div className="flex items-start justify-between"><div><p className="text-sm font-semibold">{plan.name}</p><p className="mt-1 text-xs text-slate-500">LeadX subscription</p></div><Link href={`/checkout?plan=${planKey}&interval=${interval === "annual" ? "monthly" : "annual"}`} className="text-xs font-semibold text-brand-700">Switch {interval === "annual" ? "monthly" : "annual"}</Link></div>
          <div className="mt-7 flex items-end gap-1"><span className="text-4xl font-semibold tracking-[-.045em]">${price}</span><span className="mb-1 text-sm text-slate-500">/mo</span></div>
          <p className="mt-1 text-xs text-slate-400">{interval === "annual" ? `$${yearlyTotal} billed annually` : "Billed monthly"}</p>
          <div className="my-6 border-t border-slate-100" />
          {signedIn ? <button onClick={pay} disabled={busy} className="w-full rounded-xl bg-brand-700 px-4 py-3 text-sm font-semibold text-white transition hover:bg-brand-800 disabled:opacity-50">{busy ? "Opening secure checkout…" : "Continue to secure payment"}</button> : <Link href={`/login?next=${encodeURIComponent(`/checkout?plan=${planKey}&interval=${interval}`)}`} className="block w-full rounded-xl bg-brand-700 px-4 py-3 text-center text-sm font-semibold text-white hover:bg-brand-800">Sign in to continue</Link>}
          {error && <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs leading-5 text-red-700">{error}</p>}
          <p className="mt-4 text-center text-[11px] leading-5 text-slate-400">You will be redirected to Stripe for card entry and confirmation. LeadX does not store your full card number.</p>
        </section>
      </div>
    </main>
  );
}
