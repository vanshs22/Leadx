import Link from "next/link";

export default function NotFound() {
  return (
    <main className="auth-shell">
      <section className="auth-card max-w-lg">
        <div className="brand-mark h-11 w-11 rounded-2xl text-xs">LX</div>
        <div className="mt-6 section-kicker text-brand-600 dark:text-brand-300">404 · Route not found</div>
        <h1 className="auth-title">That LeadX page doesn’t exist.</h1>
        <p className="auth-copy">The link may be old, incomplete, or no longer part of this workspace.</p>
        <div className="mt-5 flex flex-wrap gap-2">
          <Link className="btn-primary" href="/dashboard">Open dashboard</Link>
          <Link className="btn-secondary" href="/leads">Open leads</Link>
          <Link className="btn-secondary" href="/admin">Admin</Link>
        </div>
      </section>
    </main>
  );
}
