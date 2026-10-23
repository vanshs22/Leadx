"use client";

import { useEffect } from "react";

export default function GlobalRouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => console.error("LeadX route error", error), [error]);
  return (
    <main className="auth-shell">
      <section className="auth-card max-w-lg">
        <div className="brand-mark h-11 w-11 rounded-2xl text-xs">LX</div>
        <div className="mt-6 section-kicker text-rust-600 dark:text-rust-300">Workspace recovery</div>
        <h1 className="auth-title">This view hit an unexpected error.</h1>
        <p className="auth-copy">Your data is safe. Retry the view, or return to the workspace if the problem persists.</p>
        <div className="mt-5 rounded-2xl border border-rust-200 bg-rust-50 p-4 text-sm text-rust-800 dark:border-rust-500/25 dark:bg-rust-500/10 dark:text-rust-200">
          {error?.message || "Unexpected application error"}
        </div>
        <div className="mt-5 flex gap-2">
          <button aria-label="Action" className="btn-primary" onClick={() => reset()}>Retry</button>
          <a className="btn-secondary" href="/dashboard">Back to workspace</a>
        </div>
      </section>
    </main>
  );
}
