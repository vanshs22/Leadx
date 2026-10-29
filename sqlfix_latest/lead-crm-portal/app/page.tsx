import Link from "next/link";
import { Icon, type IconName } from "@/components/ui/Icon";

export const dynamic = "force-dynamic";

const proof: Array<{ icon: IconName; label: string; title: string; body: string }> = [
  { icon: "search", label: "Discover", title: "Find the right accounts", body: "Target a market, territory, or ICP without buying a giant unqualified list." },
  { icon: "brain", label: "Understand", title: "See the reason to reach out", body: "Every record carries evidence, contactability, and a clear why-now signal." },
  { icon: "growth", label: "Convert", title: "Keep momentum after send", body: "Sequences, replies, tasks, and opportunities stay connected in one operating loop." },
];

const metrics = [
  { value: "9", label: "tracked stages from discovery to delivery" },
  { value: "14-day", label: "quality guarantee on bad-lead reports" },
  { value: "8,000", label: "qualified leads a month on the Agency plan" },
  { value: "$0", label: "to start on the Free plan, no card required" },
];

const steps = [
  { title: "Define the market", body: "Choose verticals, territories and an ICP. LeadX reserves the territory so leads are not duplicated across agencies." },
  { title: "Discover and enrich", body: "Public business signals are collected, enriched with contacts, and verified before anything reaches your queue." },
  { title: "Score and gate", body: "Each account is scored and tiered. Only records that pass your quality gates are delivered." },
  { title: "Work the loop", body: "Sequences, replies, tasks and deals stay attached to the lead, so every handoff stays visible." },
];

const queue = [
  { name: "Northstar Dental", signal: "Website gap · 4.8 rating", tier: "A", score: 92 },
  { name: "Lumen Skin Studio", signal: "No booking path · 96 reviews", tier: "A", score: 88 },
  { name: "Harbor Legal", signal: "Hiring signal · owner found", tier: "B", score: 74 },
  { name: "Cedar Physio", signal: "New location · phone verified", tier: "B", score: 71 },
];

const funnel = [
  { label: "Discovered", value: 412, width: "100%" },
  { label: "Enriched", value: 268, width: "65%" },
  { label: "Verified", value: 181, width: "44%" },
  { label: "Delivered", value: 128, width: "31%" },
];

const plans = [
  { name: "Starter", price: 49, leads: "500 qualified leads / month", body: "For focused prospecting in one market." },
  { name: "Growth", price: 149, leads: "2,500 qualified leads / month", body: "For teams building a repeatable motion.", recommended: true },
  { name: "Agency", price: 399, leads: "8,000 qualified leads / month", body: "For multi-client GTM teams." },
];

export default function Landing() {
  return (
    <main className="landing-page min-h-screen overflow-hidden text-white">
      <nav className="relative z-10 mx-auto flex max-w-7xl items-center justify-between gap-4 px-5 py-5 sm:px-8 lg:px-10" aria-label="Primary">
        <Link href="/" className="flex items-center gap-3" aria-label="LeadX home">
          <span className="landing-mark">LX<span /></span>
          <span className="font-display text-lg font-semibold tracking-[-0.03em]">LeadX</span>
        </Link>
        <div className="hidden items-center gap-1 md:flex">
          <a href="#system" className="landing-nav-link">The system</a>
          <a href="#how" className="landing-nav-link">How it works</a>
          <a href="#quality" className="landing-nav-link">Quality</a>
          <Link href="/pricing" className="landing-nav-link">Pricing</Link>
        </div>
        <div className="flex items-center gap-2">
          <Link href="/login" className="landing-nav-link">Sign in</Link>
          <Link href="/sample" className="landing-nav-cta">Get a sample <Icon name="arrow" size={15} /></Link>
        </div>
      </nav>

      {/* Hero */}
      <section className="relative mx-auto grid max-w-7xl items-center gap-12 px-5 pb-20 pt-12 sm:px-8 md:pb-24 md:pt-16 lg:grid-cols-[minmax(0,.95fr)_minmax(0,1.05fr)] lg:gap-14 lg:px-10 lg:pt-20">
        <div className="landing-orbit landing-orbit-one" aria-hidden="true" />
        <div className="landing-orbit landing-orbit-two" aria-hidden="true" />
        <div className="relative z-10 max-w-2xl">
          <div className="landing-eyebrow"><span className="landing-live-dot" aria-hidden="true" /> Agency growth infrastructure</div>
          <h1 className="mt-6 font-display text-[2.625rem] font-semibold leading-[1.04] tracking-[-0.035em] text-white sm:text-[3.5rem] lg:text-[4.25rem]">
            Better leads.<br /><span className="landing-accent">Better reasons</span><br />to reach out.
          </h1>
          <p className="mt-6 max-w-xl text-base leading-7 text-brand-200 sm:text-[1.0625rem] sm:leading-8">
            LeadX turns local market signals into curated opportunities your agency can actually work, then keeps discovery, outreach, replies, and revenue moving together.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Link href="/sample" className="landing-primary">See a free sample <Icon name="arrow" size={16} /></Link>
            <Link href="/pricing" className="landing-secondary">Explore the model</Link>
          </div>
          <ul className="mt-9 flex flex-wrap gap-x-6 gap-y-2 text-[0.8125rem] font-medium text-brand-300" aria-label="Principles">
            <li className="flex items-center gap-2"><Icon name="check" size={14} className="text-signal-300" /> Evidence first</li>
            <li className="flex items-center gap-2"><Icon name="check" size={14} className="text-signal-300" /> Agency owned</li>
            <li className="flex items-center gap-2"><Icon name="check" size={14} className="text-signal-300" /> Credit protected</li>
          </ul>
        </div>

        {/* Product console preview (static illustration) */}
        <div className="relative z-10 min-w-0 lg:pl-4" aria-label="Product preview" role="img">
          <div className="landing-console">
            <div className="flex items-center justify-between border-b border-white/10 px-5 py-3.5">
              <div className="flex items-center gap-3"><span className="landing-console-dot" aria-hidden="true" /><span className="text-[0.8125rem] font-semibold text-white">Growth workspace</span></div>
              <span className="text-xs text-brand-300">Updated just now</span>
            </div>
            <div className="grid gap-4 p-5 sm:grid-cols-[minmax(0,1.25fr)_minmax(0,.75fr)]">
              <div className="min-w-0 rounded-xl border border-white/10 bg-white/[.03]">
                <div className="flex items-end justify-between gap-3 border-b border-white/10 px-4 py-3.5">
                  <div>
                    <div className="landing-console-kicker">Today’s queue</div>
                    <div className="mt-2 font-display text-3xl font-semibold tabular-nums tracking-[-0.02em] text-white">128</div>
                    <div className="mt-1 text-xs text-brand-300">high-signal accounts ready</div>
                  </div>
                  <span className="rounded-md border border-emerald-400/25 bg-emerald-400/10 px-1.5 py-0.5 text-[11px] font-semibold tabular-nums text-emerald-300">+18% wk</span>
                </div>
                <ul className="divide-y divide-white/[.06]">
                  {queue.map((row) => (
                    <li key={row.name} className="flex items-center gap-3 px-4 py-2.5">
                      <span className={`landing-tier landing-tier-${row.tier.toLowerCase()}`}>{row.tier}</span>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-[0.8125rem] font-medium text-white">{row.name}</div>
                        <div className="truncate text-[11px] text-brand-300">{row.signal}</div>
                      </div>
                      <span className="shrink-0 text-[0.8125rem] font-semibold tabular-nums text-brand-100">{row.score}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <div className="min-w-0 space-y-4">
                <div className="rounded-xl border border-white/10 bg-white/[.03] p-4">
                  <div className="landing-console-kicker">This run</div>
                  <div className="mt-3.5 space-y-3">
                    {funnel.map((f) => (
                      <div key={f.label}>
                        <div className="flex items-center justify-between text-[11px]"><span className="text-brand-200">{f.label}</span><span className="font-semibold tabular-nums text-white">{f.value}</span></div>
                        <div className="mt-1 h-1 rounded-full bg-white/[.06]"><div className="h-1 rounded-full bg-brand-400" style={{ width: f.width }} /></div>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="rounded-xl border border-signal-300/20 bg-signal-300/[.06] p-4">
                  <div className="landing-console-kicker">Why now</div>
                  <div className="mt-2.5 text-[0.8125rem] font-medium leading-5 text-white">Three accounts have a fresh conversion signal.</div>
                  <div className="mt-3 flex items-center gap-2 text-[11px] font-medium text-signal-200"><span className="h-1.5 w-1.5 rounded-full bg-signal-300" aria-hidden="true" /> Curated before contact</div>
                </div>
              </div>
            </div>
            <div className="flex items-center justify-between border-t border-white/10 px-5 py-3 text-[11px] text-brand-300"><span>Evidence-backed lead operations</span><span className="tabular-nums">Run 01 · 4 of 9 stages</span></div>
          </div>
        </div>
      </section>

      {/* Metrics strip */}
      <section className="border-y border-white/10 bg-white/[.02]" aria-label="At a glance">
        <div className="mx-auto grid max-w-7xl grid-cols-2 gap-x-4 px-5 sm:px-8 lg:grid-cols-4 lg:px-10">
          {metrics.map((m) => (
            <div key={m.label} className="py-7">
              <div className="font-display text-2xl font-semibold tabular-nums tracking-[-0.02em] text-white sm:text-[1.75rem]">{m.value}</div>
              <div className="mt-1.5 max-w-[15rem] text-[0.8125rem] leading-5 text-brand-300">{m.label}</div>
            </div>
          ))}
        </div>
      </section>

      {/* System / proof grid */}
      <section id="system" className="relative px-5 py-20 sm:px-8 lg:px-10">
        <div className="mx-auto max-w-7xl">
          <div className="max-w-2xl">
            <div className="landing-eyebrow">One system, not six tabs</div>
            <h2 className="mt-4 font-display text-3xl font-semibold tracking-[-0.03em] text-white sm:text-[2.5rem] sm:leading-[1.1]">From market signal to closed-loop revenue.</h2>
          </div>
          <div className="mt-10 grid gap-4 md:grid-cols-3">
            {proof.map((item, i) => (
              <article key={item.label} className="landing-proof">
                <div className="flex items-center justify-between">
                  <span className="landing-proof-icon"><Icon name={item.icon} size={18} /></span>
                  <span className="text-xs font-medium tabular-nums text-brand-300">0{i + 1}</span>
                </div>
                <div className="mt-8 text-[0.8125rem] font-semibold text-signal-300">{item.label}</div>
                <h3 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.02em] text-white">{item.title}</h3>
                <p className="mt-3 text-sm leading-6 text-brand-200">{item.body}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* How it works */}
      <section id="how" className="border-t border-white/10 px-5 py-20 sm:px-8 lg:px-10">
        <div className="mx-auto max-w-7xl">
          <div className="grid gap-10 lg:grid-cols-[minmax(0,.8fr)_minmax(0,1.2fr)]">
            <div className="max-w-md">
              <div className="landing-eyebrow">How it works</div>
              <h2 className="mt-4 font-display text-3xl font-semibold tracking-[-0.03em] text-white sm:text-[2.5rem] sm:leading-[1.1]">Four steps, every one of them visible.</h2>
              <p className="mt-5 text-base leading-7 text-brand-200">Each run reports what was found, what was rejected and why, so you never wonder where a lead came from.</p>
            </div>
            <ol className="grid gap-4 sm:grid-cols-2">
              {steps.map((step, i) => (
                <li key={step.title} className="rounded-xl border border-white/10 bg-white/[.02] p-6">
                  <span className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-signal-300/30 text-xs font-semibold tabular-nums text-signal-300">{i + 1}</span>
                  <h3 className="mt-5 text-base font-semibold text-white">{step.title}</h3>
                  <p className="mt-2 text-sm leading-6 text-brand-200">{step.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </section>

      {/* Quality */}
      <section id="quality" className="border-t border-white/10 px-5 py-20 sm:px-8 lg:px-10">
        <div className="mx-auto grid max-w-7xl gap-8 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
          <div>
            <div className="landing-eyebrow">Built for accountable growth</div>
            <h2 className="mt-4 max-w-2xl font-display text-3xl font-semibold tracking-[-0.03em] text-white sm:text-[2.5rem] sm:leading-[1.1]">Your agency gets the signal, the context, and the next move.</h2>
            <p className="mt-5 max-w-xl text-base leading-7 text-brand-200">Territory controls reduce duplicate delivery. Verification and evidence protect quality. The workspace keeps every handoff visible from first touch through opportunity.</p>
          </div>
          <Link href="/sample" className="landing-primary whitespace-nowrap">Open the sample pack <Icon name="arrow" size={16} /></Link>
        </div>
      </section>

      {/* Pricing teaser */}
      <section id="pricing" className="border-t border-white/10 px-5 py-20 sm:px-8 lg:px-10">
        <div className="mx-auto max-w-7xl">
          <div className="flex flex-wrap items-end justify-between gap-6">
            <div className="max-w-xl">
              <div className="landing-eyebrow">Pricing</div>
              <h2 className="mt-4 font-display text-3xl font-semibold tracking-[-0.03em] text-white sm:text-[2.5rem] sm:leading-[1.1]">Pay for qualified leads, not credits.</h2>
            </div>
            <Link href="/pricing" className="landing-secondary">Compare all plans</Link>
          </div>
          <div className="mt-10 grid gap-4 md:grid-cols-3">
            {plans.map((plan) => (
              <article key={plan.name} className={`relative rounded-xl border p-6 ${plan.recommended ? "border-signal-300/45 bg-signal-300/[.04]" : "border-white/10 bg-white/[.02]"}`}>
                <div className="flex items-center justify-between gap-3">
                  <h3 className="text-base font-semibold text-white">{plan.name}</h3>
                  {plan.recommended ? <span className="rounded-md border border-signal-300/35 bg-signal-300/10 px-1.5 py-0.5 text-[11px] font-semibold text-signal-200">Recommended</span> : null}
                </div>
                <p className="mt-1.5 text-sm text-brand-300">{plan.body}</p>
                <div className="mt-6 flex items-baseline gap-1"><span className="font-display text-4xl font-semibold tabular-nums tracking-[-0.03em] text-white">${plan.price}</span><span className="text-sm text-brand-300">/mo</span></div>
                <p className="mt-1 text-xs text-brand-300">billed monthly</p>
                <div className="mt-5 flex items-center gap-2 border-t border-white/10 pt-4 text-sm text-brand-100"><Icon name="check" size={14} className="text-signal-300" /><span className="tabular-nums">{plan.leads}</span></div>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* Closing CTA */}
      <section className="border-t border-white/10 px-5 py-16 sm:px-8 lg:px-10">
        <div className="mx-auto flex max-w-7xl flex-col items-start justify-between gap-6 rounded-xl border border-white/10 bg-white/[.03] p-8 md:flex-row md:items-center">
          <div>
            <h2 className="font-display text-2xl font-semibold tracking-[-0.02em] text-white">See ten leads from your own market.</h2>
            <p className="mt-2 text-sm text-brand-200">No signup required for the preview.</p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Link href="/sample" className="landing-primary">Get a free sample <Icon name="arrow" size={16} /></Link>
            <Link href="/login" className="landing-secondary">Sign in</Link>
          </div>
        </div>
      </section>

      <footer className="border-t border-white/10 px-5 py-7 sm:px-8 lg:px-10">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 text-xs text-brand-300 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-2"><span className="landing-mark landing-mark-small">LX<span /></span><span>LeadX · agency revenue intelligence</span></div>
          <nav className="flex flex-wrap gap-5" aria-label="Legal">
            <Link href="/pricing" className="hover:text-white">Pricing</Link>
            <Link href="/privacy" className="hover:text-white">Privacy</Link>
            <Link href="/terms" className="hover:text-white">Terms</Link>
            <Link href="/security" className="hover:text-white">Security</Link>
            <Link href="/dpa" className="hover:text-white">DPA</Link>
          </nav>
        </div>
      </footer>
    </main>
  );
}
