import Link from "next/link";

const TITLE = "Terms of Service";

const SECTIONS = [
  { id: "data-handling", title: "Data handling", body: "LeadX processes business lead and CRM data to provide discovery, enrichment, scoring, delivery and automation. Tenant data is logically isolated and requests are scoped to the authenticated tenant." },
  { id: "customer-controls", title: "Customer controls", body: "Customers can request export or deletion of their workspace data through support. Opt-outs and suppression controls should be used before outreach." },
  { id: "responsible-outreach", title: "Responsible outreach", body: "Customers remain responsible for applicable CAN-SPAM, TCPA, GDPR/ePrivacy and local marketing rules, including lawful basis, consent where required, suppression and identification requirements." },
];

const LEGAL_LINKS = [
  { href: "/privacy", label: "Privacy" },
  { href: "/terms", label: "Terms" },
  { href: "/security", label: "Security" },
  { href: "/dpa", label: "DPA" },
];

export default function Page() {
  return (
    <div className="min-h-screen bg-surface text-ink">
      <header className="border-b border-line bg-surface-2">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-5 py-4 sm:px-8">
          <Link href="/" className="flex items-center gap-2.5" aria-label="LeadX home"><span className="brand-mark">LX</span><span className="brand-wordmark">LeadX</span></Link>
          <nav className="flex flex-wrap items-center gap-1" aria-label="Legal documents">
            {LEGAL_LINKS.map((l) => <Link key={l.href} href={l.href} className={`rounded-md px-2.5 py-1.5 text-xs font-medium ${l.href === "/terms" ? "bg-surface-3 text-ink" : "text-ink-muted hover:text-ink"}`} aria-current={l.href === "/terms" ? "page" : undefined}>{l.label}</Link>)}
          </nav>
        </div>
      </header>
      <main className="mx-auto grid max-w-5xl gap-10 px-5 py-12 sm:px-8 lg:grid-cols-[180px_minmax(0,1fr)]">
        <aside className="hidden lg:block">
          <nav className="sticky top-8" aria-label="On this page">
            <div className="label">On this page</div>
            <ul className="mt-3 space-y-2 border-l border-line text-[0.8125rem]">
              {SECTIONS.map((s) => <li key={s.id}><a href={`#${s.id}`} className="-ml-px block border-l border-transparent pl-3 text-ink-muted hover:border-ink-faint hover:text-ink">{s.title}</a></li>)}
            </ul>
          </nav>
        </aside>
        <article className="max-w-[68ch]">
          <div className="eyebrow text-brand-700 dark:text-brand-300">LeadX · Legal</div>
          <h1 className="mt-2 font-display text-[2rem] font-semibold leading-tight tracking-[-0.03em] text-ink sm:text-[2.25rem]">{TITLE}</h1>
          <div className="mt-6 notice notice-neutral">This page describes LeadX&apos;s current product, data, security and compliance practices. Review with your legal/compliance adviser before relying on it for a specific jurisdiction or campaign.</div>
          <div className="mt-10 space-y-10">
            {SECTIONS.map((s, i) => (
              <section key={s.id} id={s.id} className="scroll-mt-8" aria-labelledby={`${s.id}-title`}>
                <h2 id={`${s.id}-title`} className="flex items-baseline gap-3 font-display text-lg font-semibold tracking-[-0.015em] text-ink"><span className="text-sm font-medium tabular-nums text-ink-faint">{i + 1}.</span>{s.title}</h2>
                <p className="mt-3 text-[0.9375rem] leading-7 text-ink-muted">{s.body}</p>
              </section>
            ))}
          </div>
          <footer className="mt-14 border-t border-line pt-5 text-xs text-ink-faint">Questions about this document? Contact LeadX support from your workspace.</footer>
        </article>
      </main>
    </div>
  );
}
