import Link from "next/link";
import { DOCS, LEGAL } from "@/lib/legal";

const NAV = [["privacy", "Privacy"], ["terms", "Terms"], ["dpa", "DPA"], ["security", "Security"], ["cookies", "Cookies"], ["acceptable-use", "Acceptable use"], ["subprocessors", "Subprocessors"]] as const;

export function LegalDoc({ slug }: { slug: keyof typeof DOCS }) {
  const doc = DOCS[slug];
  return (
    <div className="min-h-screen bg-surface text-ink">
      <header className="border-b border-line bg-surface-2">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3 px-5 py-4 sm:px-8">
          <Link href="/" className="flex items-center gap-2.5" aria-label="LeadX home"><span className="brand-mark">LX</span><span className="brand-wordmark">LeadX</span></Link>
          <nav className="flex flex-wrap items-center gap-1" aria-label="Legal documents">
            {NAV.map(([s, label]) => (
              <Link key={s} href={`/${s}`} aria-current={s === slug ? "page" : undefined} className={`rounded-md px-2.5 py-1.5 text-xs font-medium ${s === slug ? "bg-surface-3 text-ink" : "text-ink-muted hover:text-ink"}`}>{label}</Link>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto grid max-w-5xl gap-10 px-5 py-12 sm:px-8 lg:grid-cols-[200px_minmax(0,1fr)]">
        <aside className="hidden lg:block">
          <nav className="sticky top-8" aria-label="On this page">
            <div className="label">On this page</div>
            <ul className="mt-3 space-y-2 border-l border-line text-[0.8125rem]">
              {doc.sections.map((s) => <li key={s.id}><a href={`#${s.id}`} className="-ml-px block border-l border-transparent pl-3 text-ink-muted hover:border-ink-faint hover:text-ink">{s.title}</a></li>)}
            </ul>
          </nav>
        </aside>
        <article className="max-w-[70ch]">
          <div className="eyebrow text-brand-700 dark:text-brand-300">{LEGAL.entity} · Legal</div>
          <h1 className="mt-2 font-display text-[2rem] font-semibold leading-tight tracking-[-0.03em] text-ink sm:text-[2.25rem]">{doc.title}</h1>
          <p className="mt-2 text-sm text-ink-muted">{doc.summary} Last updated {LEGAL.updated}.</p>
          <div className="mt-6 notice notice-neutral">This document describes how LeadX works today. It is a general statement, not legal advice; have counsel review it for your jurisdiction and your outreach before relying on it.</div>
          <div className="mt-10 space-y-10">
            {doc.sections.map((s, i) => (
              <section key={s.id} id={s.id} className="scroll-mt-8" aria-labelledby={`${s.id}-title`}>
                <h2 id={`${s.id}-title`} className="flex items-baseline gap-3 font-display text-lg font-semibold tracking-[-0.015em] text-ink"><span className="text-sm font-medium tabular-nums text-ink-faint">{i + 1}.</span>{s.title}</h2>
                <div className="mt-3 space-y-3 text-[0.9375rem] leading-7 text-ink-muted">{s.body.map((p, k) => <p key={k}>{p}</p>)}</div>
              </section>
            ))}
          </div>
          <footer className="mt-14 border-t border-line pt-5 text-xs text-ink-faint">Questions? <a className="underline" href={`mailto:${LEGAL.email}`}>{LEGAL.email}</a> · <Link className="underline" href="/contact">Contact</Link></footer>
        </article>
      </main>
    </div>
  );
}
