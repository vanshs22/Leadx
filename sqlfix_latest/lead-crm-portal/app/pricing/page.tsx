"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getApiBase } from "../../lib/config";
import { Icon } from "@/components/ui/Icon";

const FALLBACK_PLANS = [
  {
    key: "free", name: "Free", price: 0, annual: 0, label: "Explore the engine",
    leads: "100 qualified leads / month", research: "10 deep research runs",
    features: ["Business discovery", "Company enrichment", "Email verification", "Basic CRM & pipeline", "Export lead data"],
    cta: "Start free", tone: "plain",
  },
  {
    key: "starter", name: "Starter", price: 49, annual: 39, label: "For focused prospecting",
    leads: "500 qualified leads / month", research: "50 deep research runs",
    features: ["Everything in Free", "Advanced discovery", "Contact enrichment", "Deep research", "ICP builder", "CRM & pipeline"],
    cta: "Start Starter", tone: "plain",
  },
  {
    key: "growth", name: "Growth", price: 149, annual: 119, label: "For teams building a repeatable motion",
    leads: "2,500 qualified leads / month", research: "300 deep research runs",
    features: ["Everything in Starter", "Multi-provider waterfalls", "BYOK enrichment", "Advanced research", "CRM integrations", "API & webhooks", "Usage analytics"],
    cta: "Choose Growth", tone: "featured",
  },
  {
    key: "agency", name: "Agency", price: 399, annual: 319, label: "For multi-client GTM teams",
    leads: "8,000 qualified leads / month", research: "1,500 deep research runs",
    features: ["Everything in Growth", "Multi-client workspaces", "10 users", "Client reporting", "White-label options", "Priority support", "Advanced provider controls"],
    cta: "Choose Agency", tone: "plain",
  },
];

export default function PricingPage() {
  const [plans, setPlans] = useState(FALLBACK_PLANS);
  const [annual, setAnnual] = useState(true);
  const [loading, setLoading] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${getApiBase()}/public/plans`, { cache: "no-store" })
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        const remote = Array.isArray(data?.plans) ? data.plans : [];
        if (!remote.length) return;
        setPlans((current) => current.map((fallback) => {
          const server = remote.find((x: any) => x.key === fallback.key);
          if (!server) return fallback;
          return { ...fallback, leads: `${server.leads_per_month.toLocaleString()} qualified leads / month`, research: `${server.research_runs.toLocaleString()} deep research runs` };
        }));
      })
      .catch(() => {});
  }, []);

  function start(plan: string) {
    setLoading(plan);
    window.location.href = plan === "free" ? "/login?plan=free" : `/checkout?plan=${plan}&interval=${annual ? "annual" : "monthly"}`;
  }

  return (
    <main className="min-h-screen bg-surface text-ink">
      <header className="border-b border-line bg-surface-2">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-5 py-4 sm:px-8">
          <Link href="/login" className="flex items-center gap-2.5" aria-label="LeadX home">
            <span className="brand-mark">LX</span>
            <span className="brand-wordmark">LeadX</span>
          </Link>
          <div className="flex items-center gap-2">
            <Link href="/sample" className="btn-ghost btn-sm hidden sm:inline-flex">Free sample</Link>
            <Link href="/login" className="btn-secondary btn-sm">Sign in</Link>
          </div>
        </div>
      </header>

      <section className="mx-auto max-w-7xl px-5 pb-20 pt-12 sm:px-8 sm:pt-16">
        <div className="mx-auto max-w-3xl text-center">
          <p className="eyebrow text-brand-700 dark:text-brand-300">Simple pricing. Serious prospecting.</p>
          <h1 className="mt-4 font-display text-[2.25rem] font-semibold leading-[1.08] tracking-[-0.032em] text-ink sm:text-[3.25rem]">Pay for qualified leads, not a maze of credits.</h1>
          <p className="mx-auto mt-5 max-w-2xl text-base leading-7 text-ink-muted">LeadX finds and enriches the businesses your team asks for. Your client-facing workspace gets the lead data; the deeper GTM intelligence stays in your operating layer.</p>
          <div className="tabs mt-8" role="group" aria-label="Billing interval">
            <button type="button" aria-pressed={!annual} onClick={() => setAnnual(false)} className={`${!annual ? "tab-active" : "tab"} px-4 py-2 text-[0.8125rem]`}>Monthly</button>
            <button type="button" aria-pressed={annual} onClick={() => setAnnual(true)} className={`${annual ? "tab-active" : "tab"} px-4 py-2 text-[0.8125rem]`}>Annual <span className="ml-1 font-semibold text-signal-600 dark:text-signal-300">save 20%</span></button>
          </div>
        </div>

        <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {plans.map((plan) => {
            const price = annual ? plan.annual : plan.price;
            const featured = plan.tone === "featured";
            return (
              <article key={plan.key} aria-label={`${plan.name} plan`} className={`relative flex flex-col rounded-xl border bg-surface-2 p-6 shadow-card ${featured ? "border-signal-400 ring-1 ring-signal-400/60 dark:border-signal-300/60 dark:ring-signal-300/30" : "border-line"}`}>
                <div className="flex items-center justify-between gap-2">
                  <h2 className="section-title text-base">{plan.name}</h2>
                  {featured && <span className="badge-accent">Most popular</span>}
                </div>
                <p className="mt-1 min-h-10 text-[0.8125rem] leading-5 text-ink-muted">{plan.label}</p>
                <div className="mt-6 flex items-baseline gap-1">
                  <span className="font-display text-[2.5rem] font-semibold leading-none tabular-nums tracking-[-0.03em] text-ink">${price}</span>
                  <span className="text-sm text-ink-muted">/mo</span>
                </div>
                {annual && plan.price > 0 ? <p className="mt-2 text-xs text-ink-faint">billed annually</p> : <p className="mt-2 text-xs text-ink-faint">{plan.price === 0 ? "No card required" : "billed monthly"}</p>}
                <div className="mt-5 space-y-1 rounded-lg border border-line bg-surface-3 px-3 py-2.5 text-[0.8125rem]">
                  <p className="font-semibold tabular-nums text-ink">{plan.leads}</p>
                  <p className="tabular-nums text-ink-muted">{plan.research}</p>
                </div>
                <ul className="mt-5 space-y-2.5 text-[0.8125rem] text-ink-muted">
                  {plan.features.map((f) => (
                    <li key={f} className="flex gap-2">
                      <Icon name="check" size={15} className={`mt-0.5 shrink-0 ${featured ? "text-signal-600 dark:text-signal-300" : "text-brand-600 dark:text-brand-300"}`} />
                      <span>{f}</span>
                    </li>
                  ))}
                </ul>
                <div className="mt-auto pt-7">
                  <button type="button" onClick={() => start(plan.key)} disabled={loading === plan.key} className={`${featured ? "btn-primary" : "btn-secondary"} w-full py-2.5`}>
                    {loading === plan.key ? "Opening…" : plan.cta}
                  </button>
                </div>
              </article>
            );
          })}
        </div>

        <div className="mx-auto mt-10 grid max-w-5xl gap-4 md:grid-cols-2">
          <div className="card-pad flex gap-3">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-300"><Icon name="security" size={16} /></span>
            <div><h3 className="section-title">14-day quality guarantee</h3><p className="section-sub">Hard bounces, dead contact details and approved bad-lead reports can return a lead credit.</p></div>
          </div>
          <div className="card-pad flex gap-3">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-300"><Icon name="report" size={16} /></span>
            <div><h3 className="section-title">Data quality is measurable</h3><p className="section-sub">Every workspace shows delivered leads, returned credits and its current data-quality percentage.</p></div>
          </div>
        </div>

        <div className="mx-auto mt-4 flex max-w-5xl flex-col items-start justify-between gap-3 rounded-xl border border-line bg-surface-2 px-6 py-5 text-[0.8125rem] text-ink-muted shadow-card sm:flex-row sm:items-center">
          <p>Need more than <span className="tabular-nums">8,000</span> qualified leads or custom security, procurement, or data terms? Enterprise plans are available.</p>
          <Link href="/login" className="btn-secondary btn-sm shrink-0">Talk to sales</Link>
        </div>

        <div className="mx-auto mt-16 max-w-3xl border-t border-line pt-10">
          <h2 className="font-display text-xl font-semibold tracking-[-0.02em] text-ink">What counts as a qualified lead?</h2>
          <p className="mt-3 text-sm leading-7 text-ink-muted">A LeadX qualified lead passes the workspace&apos;s configured discovery, data-quality and eligibility gates. Field availability can vary by market and provider; LeadX does not promise that every record contains every field.</p>
        </div>
      </section>

      <footer className="border-t border-line px-5 py-6 sm:px-8">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 text-xs text-ink-faint">
          <span>LeadX · agency revenue intelligence</span>
          <nav className="flex gap-4" aria-label="Legal"><Link href="/privacy" className="hover:text-ink">Privacy</Link><Link href="/terms" className="hover:text-ink">Terms</Link><Link href="/security" className="hover:text-ink">Security</Link></nav>
        </div>
      </footer>
    </main>
  );
}
