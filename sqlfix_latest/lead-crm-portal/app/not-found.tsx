import Link from "next/link";

export default function NotFound() {
  return (
    <main className="auth-shell">
      <section className="auth-card max-w-lg">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5"><div className="auth-logo">LX</div><span className="brand-wordmark">LeadX</span></div>
          <span className="badge-neutral tabular-nums">404</span>
        </div>
        <div className="mt-7 eyebrow text-brand-700 dark:text-brand-300">Route not found</div>
        <h1 className="mt-1.5 font-display text-[1.375rem] font-semibold tracking-[-0.02em] text-ink">That LeadX page doesn’t exist.</h1>
        <p className="auth-copy">The link may be old, incomplete, or no longer part of this workspace.</p>
        <div className="mt-6 flex flex-wrap gap-2 border-t border-line pt-5">
          <Link className="btn-primary" href="/dashboard">Open dashboard</Link>
          <Link className="btn-secondary" href="/leads">Open leads</Link>
          <Link className="btn-ghost" href="/admin">Admin</Link>
        </div>
      </section>
    </main>
  );
}
