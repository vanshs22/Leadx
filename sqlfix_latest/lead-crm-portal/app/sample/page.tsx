"use client";

import { useState } from "react";
import Link from "next/link";
import { getApiBase } from "@/lib/config";

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
    <div className="min-h-screen bg-[rgb(var(--bg))] text-ink">
      <div className="mx-auto max-w-2xl px-4 py-12">
        <div className="mb-8 text-center">
          <h1 className="text-3xl font-bold tracking-tight">Get 10 free sample leads</h1>
          <p className="mt-2 text-ink-muted">
            Exclusive local quality — no signup required for the preview.
          </p>
        </div>

        <div className="card-pad space-y-4">
          <label className="block text-xs font-medium text-ink-faint">
            Vertical
            <input
              className="input mt-1"
              value={vertical}
              onChange={(e) => setVertical(e.target.value)}
              placeholder="medspa, dental, salon…"
            />
          </label>
          <label className="block text-xs font-medium text-ink-faint">
            City / region
            <input
              className="input mt-1"
              value={geo}
              onChange={(e) => setGeo(e.target.value)}
              placeholder="Hoboken, New Jersey"
            />
          </label>
          <button
            disabled={loading || !vertical.trim()}
            className="btn-primary w-full"
            onClick={getSample}
          >
            {loading ? "Finding leads…" : "Show me 10 free leads"}
          </button>
        </div>

        {msg && (
          <div className="mt-4 rounded-xl border border-line bg-surface-2 px-4 py-2.5 text-sm text-ink-muted">
            {msg}
          </div>
        )}

        {leads.length > 0 && (
          <div className="mt-6 space-y-2">
            {leads.map((l, i) => (
              <div key={i} className="card-pad flex justify-between gap-3">
                <div>
                  <div className="font-semibold text-ink">{l.name}</div>
                  <div className="text-sm text-ink-muted">
                    {[l.city, l.state].filter(Boolean).join(", ")}
                    {l.category ? ` · ${l.category}` : ""}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-2 text-xs text-ink-faint">
                    {l.google_rating != null && <span>{l.google_rating}★</span>}
                    {l.review_count != null && <span>{l.review_count} reviews</span>}
                    {l.has_website && <span>Has website</span>}
                    {l.has_phone && <span>Has phone</span>}
                  </div>
                </div>
                <div className="text-right shrink-0">
                  {l.tier && (
                    <span className="rounded-full bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-700 dark:bg-brand-500/15 dark:text-brand-300">
                      Tier {l.tier}
                    </span>
                  )}
                  {l.score != null && (
                    <div className="mt-1 text-sm font-medium text-ink">{l.score}</div>
                  )}
                </div>
              </div>
            ))}
            <div className="card-pad mt-4 text-center">
              <p className="text-sm text-ink-muted mb-3">
                Want exclusive leads delivered daily for your agency?
              </p>
              <Link href="/login" className="btn-primary inline-block">
                Start free trial
              </Link>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
