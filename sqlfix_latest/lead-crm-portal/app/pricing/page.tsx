"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getApiBase } from "../../lib/config";

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
    <main className="min-h-screen bg-[#f7f8f6] text-[#111827]">
      <header className="mx-auto flex max-w-7xl items-center justify-between px-5 py-5 sm:px-8">
        <Link href="/login" className="flex items-center gap-2.5">
          <span className="brand-mark h-9 w-9 rounded-[10px] text-[10px]">LX</span>
          <span className="brand-wordmark">LeadX</span>
        </Link>
        <Link href="/login" className="text-sm font-semibold text-slate-600 transition hover:text-slate-950">Sign in</Link>
      </header>

      <section className="mx-auto max-w-7xl px-5 pb-16 pt-12 sm:px-8 sm:pt-16">
        <div className="mx-auto max-w-3xl text-center">
          <p className="eyebrow text-brand-700">Simple pricing. Serious prospecting.</p>
          <h1 className="mt-4 text-4xl font-semibold tracking-[-0.045em] text-slate-950 sm:text-6xl">Pay for qualified leads, not a maze of credits.</h1>
          <p className="mx-auto mt-5 max-w-2xl text-base leading-7 text-slate-600 sm:text-lg">LeadX finds and enriches the businesses your team asks for. Your client-facing workspace gets the lead data; the deeper GTM intelligence stays in your operating layer.</p>
          <div className="mt-8 inline-flex items-center gap-1 rounded-full border border-slate-200 bg-white p-1 shadow-sm">
            <button aria-label="Action" onClick={() => setAnnual(false)} className={`rounded-full px-4 py-2 text-sm font-semibold ${!annual ? "bg-slate-950 text-white" : "text-slate-600 hover:text-slate-950"}`}>Monthly</button>
            <button aria-label="Action" onClick={() => setAnnual(true)} className={`rounded-full px-4 py-2 text-sm font-semibold ${annual ? "bg-slate-950 text-white" : "text-slate-600 hover:text-slate-950"}`}>Annual <span className="ml-1 text-brand-300">save 20%</span></button>
          </div>
        </div>

        <div className="mt-14 grid gap-4 lg:grid-cols-4">
          {plans.map((plan) => {
            const price = annual ? plan.annual : plan.price;
            const featured = plan.tone === "featured";
            return (
              <article key={plan.key} className={`relative flex min-h-[520px] flex-col rounded-2xl border bg-white p-6 shadow-[0_1px_2px_rgba(15,23,42,.04)] ${featured ? "border-brand-600 ring-1 ring-brand-600" : "border-slate-200"}`}>
                {featured && <div className="absolute -top-3 left-5 rounded-full bg-brand-700 px-3 py-1 text-[10px] font-bold uppercase tracking-[.13em] text-white">Most popular</div>}
                <div>
                  <div className="text-sm font-semibold text-slate-950">{plan.name}</div>
                  <p className="mt-1 min-h-10 text-sm leading-5 text-slate-500">{plan.label}</p>
                  <div className="mt-6 flex items-end gap-1"><span className="text-4xl font-semibold tracking-[-.045em]">${price}</span><span className="mb-1 text-sm text-slate-500">/mo</span></div>
                  {annual && plan.price > 0 ? <p className="mt-1 text-xs text-slate-400">billed annually</p> : <p className="mt-1 text-xs text-slate-400">{plan.price === 0 ? "No card required" : "billed monthly"}</p>}
                </div>
                <div className="my-6 border-t border-slate-100" />
                <div className="space-y-2 text-sm"><p className="font-semibold text-slate-900">{plan.leads}</p><p className="text-slate-500">{plan.research}</p></div>
                <ul className="mt-6 space-y-3 text-sm text-slate-600">{plan.features.map((f) => <li key={f} className="flex gap-2"><span className="mt-0.5 text-brand-700">✓</span><span>{f}</span></li>)}</ul>
                <button aria-label="Action" onClick={() => start(plan.key)} disabled={loading === plan.key} className={`mt-auto w-full rounded-xl px-4 py-3 text-sm font-semibold transition ${featured ? "bg-brand-700 text-white hover:bg-brand-800" : "border border-slate-300 bg-white text-slate-900 hover:border-slate-500 hover:bg-slate-50"}`}>
                  {loading === plan.key ? "Opening…" : plan.cta}
                </button>
              </article>
            );
          })}
        </div>

        <div className="mx-auto mt-10 grid max-w-5xl gap-4 md:grid-cols-2"><div className="rounded-2xl border border-slate-200 bg-white px-6 py-5 text-sm text-slate-600"><b className="text-slate-950">14-day quality guarantee</b><p className="mt-2">Hard bounces, dead contact details and approved bad-lead reports can return a lead credit.</p></div><div className="rounded-2xl border border-slate-200 bg-white px-6 py-5 text-sm text-slate-600"><b className="text-slate-950">Data quality is measurable</b><p className="mt-2">Every workspace shows delivered leads, returned credits and its current data-quality percentage.</p></div></div>

        <div className="mx-auto mt-10 max-w-5xl rounded-2xl border border-slate-200 bg-white px-6 py-5 text-center text-sm text-slate-500">
          Need more than 8,000 qualified leads or custom security, procurement, or data terms? <Link href="/login" className="font-semibold text-slate-900 hover:underline">Talk to sales</Link> for Enterprise.
        </div>

        <div className="mx-auto mt-16 max-w-4xl border-t border-slate-200 pt-10">
          <h2 className="text-xl font-semibold tracking-tight">What counts as a qualified lead?</h2>
          <p className="mt-3 text-sm leading-6 text-slate-600">A LeadX qualified lead passes the workspace's configured discovery, data-quality and eligibility gates. Field availability can vary by market and provider; LeadX does not promise that every record contains every field.</p>
        </div>
      </section>
    </main>
  );
}
