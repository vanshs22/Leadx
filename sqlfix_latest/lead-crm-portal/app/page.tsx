import Link from "next/link";
import { Icon } from "@/components/ui/Icon";

export const dynamic = "force-dynamic";

const proof = [
  { icon: "search" as const, label: "Discover", title: "Find the right accounts", body: "Target a market, territory, or ICP without buying a giant unqualified list." },
  { icon: "brain" as const, label: "Understand", title: "See the reason to reach out", body: "Every record carries evidence, contactability, and a clear why-now signal." },
  { icon: "growth" as const, label: "Convert", title: "Keep momentum after send", body: "Sequences, replies, tasks, and opportunities stay connected in one operating loop." },
];

export default function Landing() {
  return (
    <main className="landing-page min-h-screen overflow-hidden bg-[#081514] text-[#f4f7f2]">
      <nav className="relative z-10 mx-auto flex max-w-7xl items-center justify-between px-5 py-5 sm:px-8 lg:px-10">
        <Link href="/" className="flex items-center gap-3" aria-label="LeadX home">
          <span className="landing-mark">LX<span /></span>
          <span className="font-display text-lg font-bold tracking-[-.04em]">LeadX</span>
        </Link>
        <div className="hidden items-center gap-8 text-sm text-[#a9c4bc] md:flex">
          <a href="#system" className="transition hover:text-white">The system</a>
          <a href="#quality" className="transition hover:text-white">Quality</a>
          <Link href="/pricing" className="transition hover:text-white">Pricing</Link>
        </div>
        <div className="flex items-center gap-2">
          <Link href="/login" className="landing-nav-link">Sign in</Link>
          <Link href="/sample" className="landing-nav-cta">Get a sample <Icon name="arrow" size={15} /></Link>
        </div>
      </nav>

      <section className="relative mx-auto grid max-w-7xl items-center gap-14 px-5 pb-20 pt-14 sm:px-8 md:pb-28 md:pt-20 lg:grid-cols-[.9fr_1.1fr] lg:px-10 lg:pt-24">
        <div className="landing-orbit landing-orbit-one" />
        <div className="landing-orbit landing-orbit-two" />
        <div className="relative z-10 max-w-2xl">
          <div className="landing-eyebrow"><span className="landing-live-dot" /> Agency growth infrastructure</div>
          <h1 className="mt-6 font-display text-[3.25rem] font-semibold leading-[.98] tracking-[-.065em] text-white sm:text-6xl lg:text-[5.2rem]">
            Better leads.<br /><span className="landing-accent">Better reasons</span><br />to reach out.
          </h1>
          <p className="mt-7 max-w-xl text-base leading-7 text-[#a9c4bc] sm:text-lg">
            LeadX turns local market signals into curated opportunities your agency can actually work—then keeps discovery, outreach, replies, and revenue moving together.
          </p>
          <div className="mt-9 flex flex-wrap items-center gap-3">
            <Link href="/sample" className="landing-primary">See a free sample <Icon name="arrow" size={17} /></Link>
            <Link href="/pricing" className="landing-secondary">Explore the model</Link>
          </div>
          <div className="mt-9 flex flex-wrap gap-x-6 gap-y-2 text-[11px] font-semibold uppercase tracking-[.16em] text-[#71958b]">
            <span className="flex items-center gap-2"><Icon name="check" size={14} /> Evidence first</span>
            <span className="flex items-center gap-2"><Icon name="check" size={14} /> Agency owned</span>
            <span className="flex items-center gap-2"><Icon name="check" size={14} /> Credit protected</span>
          </div>
        </div>

        <div className="relative z-10 lg:pl-5">
          <div className="landing-console">
            <div className="flex items-center justify-between border-b border-white/10 px-5 py-4">
              <div className="flex items-center gap-3"><span className="landing-console-dot" /><span className="text-xs font-semibold tracking-wide text-white">Growth workspace</span></div>
              <span className="font-mono text-[10px] uppercase tracking-[.16em] text-[#78b9aa]">Live signal</span>
            </div>
            <div className="grid gap-4 p-5 sm:grid-cols-[1.1fr_.9fr]">
              <div className="rounded-2xl border border-white/10 bg-white/[.045] p-4">
                <div className="flex items-start justify-between"><div><div className="landing-console-kicker">Today’s queue</div><div className="mt-2 font-display text-4xl font-semibold tracking-tight text-white">128</div><div className="mt-1 text-xs text-[#87aaa0]">high-signal accounts ready</div></div><span className="rounded-full bg-[#e58a4c]/15 px-2 py-1 text-[10px] font-bold text-[#f2ad7c]">+18%</span></div>
                <div className="mt-8 space-y-3">
                  {[['Northstar Dental', 'Website gap · 4.8★', 'A'], ['Lumen Skin Studio', 'No booking path · 96 reviews', 'A'], ['Harbor Legal', 'Hiring signal · owner found', 'B']].map(([name, signal, tier]) => <div key={name} className="flex items-center gap-3 rounded-xl border border-white/8 bg-black/10 p-3"><span className={`landing-tier landing-tier-${tier.toLowerCase()}`}>{tier}</span><div className="min-w-0"><div className="truncate text-xs font-semibold text-white">{name}</div><div className="truncate text-[10px] text-[#83a69b]">{signal}</div></div><Icon name="arrow" size={14} className="ml-auto shrink-0 text-[#63958a]" /></div>)}
                </div>
              </div>
              <div className="space-y-4">
                <div className="rounded-2xl border border-[#3fc0a8]/25 bg-[#3fc0a8]/[.08] p-4"><div className="landing-console-kicker text-[#7fd9c4]">Operating loop</div><div className="mt-4 space-y-3">{[['Discover', '42 accounts'], ['Curate', '18 verified'], ['Engage', '06 replies']].map(([label, value], i) => <div key={label} className="flex items-center gap-3"><span className="flex h-6 w-6 items-center justify-center rounded-full border border-[#5acdb5]/40 text-[10px] font-bold text-[#8be1cf]">{i + 1}</span><div><div className="text-xs font-semibold text-white">{label}</div><div className="text-[10px] text-[#7da89d]">{value}</div></div></div>)}</div></div>
                <div className="rounded-2xl border border-white/10 bg-white/[.045] p-4"><div className="landing-console-kicker">Why now</div><div className="mt-3 text-sm font-semibold leading-5 text-white">Three accounts have a fresh conversion signal.</div><div className="mt-4 flex items-center gap-2 text-[10px] font-semibold text-[#e8a273]"><span className="h-1.5 w-1.5 rounded-full bg-[#e58a4c]" /> Curated before contact</div></div>
              </div>
            </div>
            <div className="flex items-center justify-between border-t border-white/10 px-5 py-3 text-[10px] text-[#72978d]"><span>Evidence-backed lead operations</span><span className="font-mono">LEADX / 01</span></div>
          </div>
        </div>
      </section>

      <section id="system" className="relative border-y border-white/10 bg-[#0b201d] px-5 py-16 sm:px-8 lg:px-10">
        <div className="mx-auto max-w-7xl"><div className="max-w-xl"><div className="landing-eyebrow">One system, not six tabs</div><h2 className="mt-4 font-display text-3xl font-semibold tracking-[-.04em] text-white sm:text-4xl">From market signal to closed-loop revenue.</h2></div><div className="mt-10 grid gap-4 md:grid-cols-3">{proof.map((item, i) => <div key={item.label} className="landing-proof"><div className="flex items-center justify-between"><span className="landing-proof-icon"><Icon name={item.icon} size={19} /></span><span className="font-mono text-[10px] text-[#537970]">0{i + 1}</span></div><div className="mt-8 text-[10px] font-bold uppercase tracking-[.16em] text-[#73b6a7]">{item.label}</div><h3 className="mt-2 font-display text-xl font-semibold tracking-[-.03em] text-white">{item.title}</h3><p className="mt-3 text-sm leading-6 text-[#91b2a8]">{item.body}</p></div>)}</div></div>
      </section>

      <section id="quality" className="mx-auto grid max-w-7xl gap-8 px-5 py-20 sm:px-8 md:grid-cols-[1fr_auto] md:items-end lg:px-10">
        <div><div className="landing-eyebrow">Built for accountable growth</div><h2 className="mt-4 max-w-2xl font-display text-3xl font-semibold tracking-[-.04em] text-white sm:text-4xl">Your agency gets the signal, the context, and the next move.</h2><p className="mt-5 max-w-xl text-base leading-7 text-[#9ab9b0]">Territory controls reduce duplicate delivery. Verification and evidence protect quality. The workspace keeps every handoff visible from first touch through opportunity.</p></div>
        <Link href="/sample" className="landing-primary whitespace-nowrap">Open the sample pack <Icon name="arrow" size={17} /></Link>
      </section>

      <footer className="border-t border-white/10 px-5 py-7 sm:px-8 lg:px-10"><div className="mx-auto flex max-w-7xl flex-col gap-4 text-xs text-[#73968d] sm:flex-row sm:items-center sm:justify-between"><div className="flex items-center gap-2"><span className="landing-mark landing-mark-small">LX<span /></span><span>LeadX · agency revenue intelligence</span></div><div className="flex gap-5"><Link href="/privacy" className="hover:text-white">Privacy</Link><Link href="/terms" className="hover:text-white">Terms</Link><Link href="/security" className="hover:text-white">Security</Link></div></div></footer>
    </main>
  );
}
