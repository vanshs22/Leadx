"use client";

import { useEffect, useState } from "react";
import { admin } from "@/lib/admin";

/**
 * Territory availability — which vertical+geo+offer combos are locked.
 * Useful during sales calls: "Newark's taken, Jersey City's open."
 */
export default function AdminTerritoryPage() {
  const [vertical, setVertical] = useState("");
  const [geo, setGeo] = useState("");
  const [data, setData] = useState<{ locked: any[]; total_active_icps: number; note?: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      // Prefer admin endpoint if available; fall back to CRM territory via a tenant-less admin route
      const q = new URLSearchParams();
      if (vertical.trim()) q.set("vertical", vertical.trim());
      if (geo.trim()) q.set("geo", geo.trim());
      const res = await admin.territory(q.toString());
      setData(res);
    } catch (e: any) {
      setError(e.message || "Failed to load territory");
      setData(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="page">
      <header>
        <h1 className="page-title">Territory availability</h1>
        <p className="page-sub">
          Exclusive vertical + geo + offer locks. Open = no exclusive ICP for that combo.
        </p>
      </header>

      <div className="card-pad flex flex-wrap items-end gap-3">
        <label className="block text-xs font-medium text-ink-faint">
          Vertical
          <input
            className="input mt-1"
            placeholder="medspa"
            value={vertical}
            onChange={(e) => setVertical(e.target.value)}
          />
        </label>
        <label className="block text-xs font-medium text-ink-faint">
          Geo
          <input
            className="input mt-1"
            placeholder="Hoboken"
            value={geo}
            onChange={(e) => setGeo(e.target.value)}
          />
        </label>
        <button aria-label="Action" className="btn-primary" disabled={loading} onClick={load}>
          {loading ? "Loading…" : "Check"}
        </button>
      </div>

      {error && (
        <div className="rounded-xl border border-rust-200 bg-rust-50 px-4 py-2.5 text-sm text-rust-700 dark:border-rust-500/30 dark:bg-rust-500/10 dark:text-rust-300">
          {error}
        </div>
      )}

      {data && (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-3 text-sm text-ink-muted">
            <span>
              Active ICPs: <strong className="text-ink">{data.total_active_icps}</strong>
            </span>
            <span>
              Locked combos: <strong className="text-ink">{data.locked?.length || 0}</strong>
            </span>
            {data.note && <span className="text-ink-faint">{data.note}</span>}
          </div>

          {(data.locked || []).length === 0 ? (
            <div className="card-pad text-sm text-ink-muted">
              No exclusive locks match this filter — territory is open.
            </div>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-line">
              <table className="w-full text-left text-sm">
                <thead className="bg-surface-2 text-xs uppercase tracking-wide text-ink-faint">
                  <tr>
                    <th className="px-4 py-3">Vertical</th>
                    <th className="px-4 py-3">Geo</th>
                    <th className="px-4 py-3">Offer</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3">ICPs</th>
                  </tr>
                </thead>
                <tbody>
                  {data.locked.map((row: any, i: number) => (
                    <tr key={i} className="border-t border-line">
                      <td className="px-4 py-3 font-medium text-ink">{row.vertical}</td>
                      <td className="px-4 py-3">{row.geo}</td>
                      <td className="px-4 py-3">{row.offer_category}</td>
                      <td className="px-4 py-3">
                        <span className="rounded-full bg-rust-50 px-2 py-0.5 text-xs font-medium text-rust-700 dark:bg-rust-500/15 dark:text-rust-300">
                          {row.status || "locked"}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-ink-muted">
                        {(row.icp_names || []).join(", ") || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
