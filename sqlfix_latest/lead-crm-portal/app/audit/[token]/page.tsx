"use client";
import { useEffect, useState } from "react";
import { getApiBase } from "@/lib/config";
import { Icon } from "@/components/ui/Icon";

function AuditBrand() {
  return (
    <div className="flex items-center gap-2.5">
      <span className="brand-mark">LX</span>
      <span className="brand-wordmark">LeadX</span>
    </div>
  );
}

export default function AuditPage({ params }: { params: { token: string } }) {
  const [d, setD] = useState<any>();
  const [e, setE] = useState("");
  useEffect(() => {
    fetch(`${getApiBase()}/crm/public/audits/${params.token}`)
      .then(async (r) => { if (!r.ok) throw new Error("Audit unavailable"); return r.json(); })
      .then((x) => setD(x.audit))
      .catch((x) => setE(x.message || "Audit unavailable"));
  }, [params.token]);

  if (e) {
    return (
      <main className="auth-shell">
        <section className="auth-card text-center" role="alert">
          <span className="mx-auto flex h-10 w-10 items-center justify-center rounded-full border border-rust-200 bg-rust-50 text-rust-700 dark:border-rust-400/25 dark:bg-rust-400/10 dark:text-rust-300"><Icon name="alert" size={18} /></span>
          <h1 className="auth-title">Audit unavailable</h1>
          <p className="auth-copy">{e}</p>
          <p className="mt-4 text-xs text-ink-faint">The link may have expired. Ask the agency that shared it for a fresh copy.</p>
        </section>
      </main>
    );
  }

  if (!d) {
    return (
      <main className="min-h-screen bg-surface text-ink">
        <div className="mx-auto max-w-4xl px-5 py-12 sm:px-8" aria-busy="true" aria-live="polite">
          <span className="sr-only">Loading audit…</span>
          <div className="skeleton h-4 w-40" />
          <div className="skeleton mt-4 h-9 w-2/3" />
          <div className="skeleton mt-3 h-4 w-1/2" />
          <div className="mt-8 grid gap-4 md:grid-cols-2">{[0, 1, 2, 3].map((i) => <div key={i} className="skeleton h-32 rounded-xl" />)}</div>
        </div>
      </main>
    );
  }

  const s = d.sections || {};
  return (
    <main className="min-h-screen bg-surface text-ink">
      <header className="border-b border-line bg-surface-2">
        <div className="mx-auto flex max-w-4xl items-center justify-between gap-4 px-5 py-4 sm:px-8">
          <AuditBrand />
          <span className="badge-neutral">Shared report</span>
        </div>
      </header>
      <div className="mx-auto max-w-4xl px-5 py-12 sm:px-8">
        <div className="eyebrow text-brand-700 dark:text-brand-300">LeadX Prospect Audit</div>
        <h1 className="mt-2 font-display text-[2rem] font-semibold leading-tight tracking-[-0.03em] text-ink sm:text-[2.5rem]">{d.title || "Local growth audit"}</h1>
        <p className="mt-3 max-w-2xl text-[0.9375rem] leading-7 text-ink-muted">A concise review of the prospect&apos;s digital presence and conversion opportunities.</p>
        <div className="mt-8 grid gap-4 md:grid-cols-2">
          {Object.entries(s).map(([k, v]: any) => (
            <section key={k} className="card-pad">
              <h2 className="label">{k.replace(/_/g, " ")}</h2>
              <div className="mt-2 break-words text-sm leading-6 text-ink">{typeof v === "string" ? v : JSON.stringify(v)}</div>
            </section>
          ))}
        </div>
        <div className="mt-8 rounded-xl border border-brand-200 bg-brand-50 p-5 dark:border-brand-500/30 dark:bg-brand-500/10">
          <h2 className="section-title">Recommended next step</h2>
          <p className="mt-1.5 text-sm leading-6 text-ink-muted">Use the findings above to prioritize the highest-impact fixes and measure the change.</p>
        </div>
        <p className="mt-10 border-t border-line pt-5 text-xs text-ink-faint">Prepared with LeadX · agency revenue intelligence</p>
      </div>
    </main>
  );
}
