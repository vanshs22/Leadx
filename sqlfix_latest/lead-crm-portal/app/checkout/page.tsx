"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { billing } from "@/lib/api";
import { getSessionFast } from "@/lib/auth";
import { Icon } from "@/components/ui/Icon";

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
    return (
      <main className="auth-shell">
        <section className="auth-card max-w-lg text-center" role="status" aria-live="polite">
          <span className="mx-auto flex h-11 w-11 items-center justify-center rounded-full border border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-400/25 dark:bg-emerald-400/10 dark:text-emerald-300"><Icon name="check" size={20} /></span>
          <h1 className="auth-title">Payment received</h1>
          <p className="auth-copy">Your workspace is being upgraded. Stripe will confirm the subscription and LeadX will apply the new plan automatically.</p>
          <Link href="/dashboard" className="btn-primary mt-6">Open workspace</Link>
        </section>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-surface text-ink">
      <header className="border-b border-line bg-surface-2">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-4 sm:px-8">
          <Link href="/pricing" className="flex items-center gap-2.5" aria-label="LeadX pricing"><span className="brand-mark">LX</span><span className="brand-wordmark">LeadX</span></Link>
          <Link href="/pricing" className="btn-ghost btn-sm">Back to pricing</Link>
        </div>
      </header>
      <div className="mx-auto grid max-w-5xl gap-8 px-5 py-12 sm:px-8 lg:grid-cols-[minmax(0,1fr)_400px]">
        <section className="pt-2">
          <p className="eyebrow inline-flex items-center gap-1.5 text-brand-700 dark:text-brand-300"><Icon name="security" size={13} /> Secure checkout</p>
          <h1 className="mt-3 font-display text-[2rem] font-semibold leading-tight tracking-[-0.03em] text-ink sm:text-[2.5rem]">Upgrade your LeadX workspace.</h1>
          <p className="mt-4 max-w-xl text-base leading-7 text-ink-muted">One subscription covers discovery, enrichment, research and the CRM workspace. Your final card details are handled by Stripe.</p>
          <div className="mt-8 grid gap-3 sm:grid-cols-2">
            <div className="card-pad">
              <p className="stat-label">Included</p>
              <p className="mt-2 text-sm font-semibold tabular-nums text-ink">{plan.leads}</p>
            </div>
            <div className="card-pad">
              <p className="stat-label">Billing</p>
              <p className="mt-2 text-sm font-semibold text-ink">{interval === "annual" ? "Annual · 20% savings" : "Monthly · cancel anytime"}</p>
            </div>
          </div>
        </section>

        <section className="card self-start p-6" aria-label="Order summary">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2"><h2 className="section-title text-base">{plan.name}</h2>{planKey === "growth" ? <span className="badge-accent">Most popular</span> : null}</div>
              <p className="mt-1 text-xs text-ink-faint">LeadX subscription</p>
            </div>
            <Link href={`/checkout?plan=${planKey}&interval=${interval === "annual" ? "monthly" : "annual"}`} className="link text-xs">Switch {interval === "annual" ? "monthly" : "annual"}</Link>
          </div>
          <div className="mt-6 flex items-baseline gap-1"><span className="font-display text-[2.5rem] font-semibold leading-none tabular-nums tracking-[-0.03em]">${price}</span><span className="text-sm text-ink-muted">/mo</span></div>
          <p className="mt-2 text-xs tabular-nums text-ink-faint">{interval === "annual" ? `$${yearlyTotal} billed annually` : "Billed monthly"}</p>
          <div className="my-6 border-t border-line" />
          {signedIn ? (
            <button type="button" onClick={pay} disabled={busy} className="btn-primary w-full py-2.5">{busy ? "Opening secure checkout…" : "Continue to secure payment"}</button>
          ) : (
            <Link href={`/login?next=${encodeURIComponent(`/checkout?plan=${planKey}&interval=${interval}`)}`} className="btn-primary w-full py-2.5">Sign in to continue</Link>
          )}
          {error && <div className="mt-3 notice notice-danger" role="alert">{error}</div>}
          <p className="mt-4 flex items-start justify-center gap-1.5 text-center text-[11px] leading-5 text-ink-faint"><Icon name="security" size={12} className="mt-1 shrink-0" />You will be redirected to Stripe for card entry and confirmation. LeadX does not store your full card number.</p>
        </section>
      </div>
    </main>
  );
}
