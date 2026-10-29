"use client";

import { useEffect } from "react";

export default function GlobalRouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => console.error("LeadX route error", error), [error]);
  return (
    <main className="auth-shell">
      <section className="auth-card max-w-lg">
        <div className="flex items-center gap-2.5"><div className="auth-logo">LX</div><span className="brand-wordmark">LeadX</span></div>
        <div className="mt-7 eyebrow text-rust-700 dark:text-rust-300">Workspace recovery</div>
        <h1 className="mt-1.5 font-display text-[1.375rem] font-semibold tracking-[-0.02em] text-ink">This view hit an unexpected error.</h1>
        <p className="auth-copy">Your data is safe. Retry the view, or return to the workspace if the problem persists.</p>
        <div className="mt-5 notice notice-danger flex-col !items-start gap-1" role="alert">
          <span className="break-words">{error?.message || "Unexpected application error"}</span>
          {error?.digest ? <span className="text-[11px] opacity-75">Reference: <code className="font-mono">{error.digest}</code></span> : null}
        </div>
        <div className="mt-6 flex flex-wrap gap-2 border-t border-line pt-5">
          <button type="button" className="btn-primary" onClick={() => reset()}>Retry</button>
          <a className="btn-secondary" href="/dashboard">Back to workspace</a>
        </div>
      </section>
    </main>
  );
}
