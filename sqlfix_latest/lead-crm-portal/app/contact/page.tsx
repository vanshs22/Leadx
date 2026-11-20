import Link from "next/link";
import { LEGAL } from "@/lib/legal";

export const metadata = { title: "Contact · LeadX", description: "How to reach LeadX for sales, support, privacy and security." };

export default function ContactPage() {
  const rows = [
    ["Sales and plans", LEGAL.email, "Questions about plans, agency pricing or a demo."],
    ["Support", LEGAL.email, "Signed-in customers get faster help from the Help button inside the app."],
    ["Privacy and opt-outs", LEGAL.email, "Access, deletion or suppression requests for any contact data."],
    ["Security", LEGAL.securityEmail, "Report a vulnerability. See /.well-known/security.txt."],
  ];
  return (
    <div className="min-h-screen bg-surface text-ink">
      <header className="border-b border-line bg-surface-2"><div className="mx-auto flex max-w-3xl items-center justify-between px-5 py-4"><Link href="/" className="flex items-center gap-2.5" aria-label="LeadX home"><span className="brand-mark">LX</span><span className="brand-wordmark">LeadX</span></Link><Link href="/login" className="text-sm text-ink-muted hover:text-ink">Sign in</Link></div></header>
      <main className="mx-auto max-w-3xl px-5 py-12">
        <h1 className="font-display text-3xl font-semibold tracking-[-0.03em]">Contact</h1>
        <dl className="mt-8 divide-y divide-line rounded-lg border border-line">
          {rows.map(([t, e, d]) => (
            <div key={t} className="grid gap-1 p-4 sm:grid-cols-[180px_1fr]"><dt className="font-medium">{t}</dt><dd><a className="text-brand-700 underline dark:text-brand-300" href={`mailto:${e}`}>{e}</a><p className="mt-1 text-sm text-ink-muted">{d}</p></dd></div>
          ))}
        </dl>
        {LEGAL.address && <p className="mt-6 text-sm text-ink-muted">{LEGAL.entity}, {LEGAL.address}</p>}
      </main>
    </div>
  );
}
