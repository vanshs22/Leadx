"use client";

import { useState } from "react";
import Link from "next/link";
import { getApiBase } from "@/lib/config";
import { Icon } from "@/components/ui/Icon";

/**
 * Public "get 10 free sample leads" landing page.
 * Hits the unauthenticated sample-pack endpoint.
 */
export default function PublicSamplePage() {
  const [vertical, setVertical] = useState("medspa");
  const [geo, setGeo] = useState("Hoboken, New Jersey");
  const [loading, setLoading] = useState(false);
  const [leads, setLeads] = useState<any[]>([]);
  const [msg, setMsg] = useState("");

  async function getSample() {
    setLoading(true);
    setMsg("");
    setLeads([]);
    try {
      const base = getApiBase();
      const res = await fetch(`${base}/crm/sample-pack/public`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          vertical: vertical.trim(),
          geo: geo.trim(),
          limit: 10,
          min_score: 65,
        }),
      });
      if (!res.ok) {
        const t = await res.text();
        throw new Error(t || res.statusText);
      }
      const data = await res.json();
      setLeads(data.leads || []);
      setMsg(`Here are ${data.count || 0} sample leads for ${vertical} in ${geo}.`);
    } catch (e: any) {
      setMsg(e.message || "Could not load sample");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-surface text-ink">
      <header className="border-b border-line bg-surface-2">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-5 py-4 sm:px-8">
          <Link href="/" className="flex items-center gap-2.5" aria-label="LeadX home"><span className="brand-mark">LX</span><span className="brand-wordmark">LeadX</span></Link>
          <div className="flex items-center gap-2">
            <Link href="/pricing" className="btn-ghost btn-sm">Pricing</Link>
            <Link href="/login" className="btn-secondary btn-sm">Sign in</Link>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-2xl px-5 py-12 sm:px-8">
        <div className="mb-8 text-center">
          <p className="eyebrow text-brand-700 dark:text-brand-300">Free sample pack</p>
          <h1 className="mt-3 font-display text-[2rem] font-semibold leading-tight tracking-[-0.03em] text-ink sm:text-[2.5rem]">Get 10 free sample leads</h1>
          <p className="mx-auto mt-3 max-w-md text-[0.9375rem] leading-7 text-ink-muted">
            Exclusive local quality — no signup required for the preview.
          </p>
        </div>

        <div className="card-pad space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block">
              <span className="field-label">Vertical</span>
              <input
                className="input"
                value={vertical}
                onChange={(e) => setVertical(e.target.value)}
                placeholder="medspa, dental, salon…"
              />
            </label>
            <label className="block">
              <span className="field-label">City / region</span>
              <input
                className="input"
                value={geo}
                onChange={(e) => setGeo(e.target.value)}
                placeholder="Hoboken, New Jersey"
              />
            </label>
          </div>
          <button
            type="button"
            disabled={loading || !vertical.trim()}
            className="btn-primary w-full py-2.5"
            onClick={getSample}
          >
            {loading ? "Finding leads…" : "Show me 10 free leads"}
          </button>
          <p className="field-hint text-center">Preview leads are scored 65 or higher.</p>
        </div>

        {msg && (
          <div className={`mt-4 notice ${leads.length > 0 ? "notice-neutral" : "notice-warning"}`} role="status" aria-live="polite">
            {msg}
          </div>
        )}

        {loading && (
          <div className="mt-6 space-y-2" aria-hidden="true">
            {[0, 1, 2].map((i) => <div key={i} className="skeleton h-[76px] rounded-xl" />)}
          </div>
        )}

        {leads.length > 0 && (
          <div className="mt-6 space-y-4">
            <ul className="panel divide-y divide-line">
              {leads.map((l, i) => (
                <li key={i} className="flex items-start justify-between gap-4 px-5 py-4">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-ink">{l.name}</div>
                    <div className="mt-0.5 truncate text-[0.8125rem] text-ink-muted">
                      {[l.city, l.state].filter(Boolean).join(", ")}
                      {l.category ? ` · ${l.category}` : ""}
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {l.google_rating != null && <span className="badge-neutral tabular-nums">{l.google_rating} rating</span>}
                      {l.review_count != null && <span className="badge-neutral tabular-nums">{l.review_count} reviews</span>}
                      {l.has_website && <span className="badge-neutral"><Icon name="globe" size={11} /> Has website</span>}
                      {l.has_phone && <span className="badge-neutral"><Icon name="phone" size={11} /> Has phone</span>}
                    </div>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1.5 text-right">
                    {l.tier && (
                      <span className={String(l.tier).toUpperCase() === "A" ? "badge-accent" : "badge-brand"}>
                        Tier {l.tier}
                      </span>
                    )}
                    {l.score != null && (
                      <div className="text-sm font-semibold tabular-nums text-ink">{l.score}<span className="ml-0.5 text-xs font-normal text-ink-faint">score</span></div>
                    )}
                  </div>
                </li>
              ))}
            </ul>
            <div className="card-pad flex flex-col items-center justify-between gap-3 text-center sm:flex-row sm:text-left">
              <p className="text-sm text-ink-muted">
                Want exclusive leads delivered daily for your agency?
              </p>
              <Link href="/login" className="btn-primary shrink-0">
                Start free trial
              </Link>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
