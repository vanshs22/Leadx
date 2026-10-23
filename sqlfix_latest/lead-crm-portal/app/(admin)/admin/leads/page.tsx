"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { admin } from "@/lib/admin";

export default function AdminLeadsClientsPage() {
  const [clients, setClients] = useState<any[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    admin
      .leadsClients()
      .then((d) => setClients(d.clients || []))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (error) return <div className="p-8 text-rust-500">{error}</div>;

  return (
    <div className="space-y-6 p-8">
      <header>
        <h1 className="text-2xl font-semibold text-ink">Leads by client</h1>
        <p className="text-sm text-ink-muted">
          Open a client to see every lead assigned to them
        </p>
      </header>

      {loading && <p className="text-sm text-ink-faint">Loading…</p>}

      <div className="overflow-x-auto rounded-xl border border-line">
        <table className="w-full min-w-[640px] text-left text-sm">
          <thead className="border-b border-line bg-surface-2 text-xs uppercase text-ink-faint">
            <tr>
              <th className="px-4 py-3">Client</th>
              <th className="px-4 py-3">Plan</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Leads</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {clients.map((c) => (
              <tr key={c.id} className="hover:bg-surface-3/50">
                <td className="px-4 py-3 font-medium text-ink">
                  {c.brand_name || c.name || c.id}
                </td>
                <td className="px-4 py-3 text-ink-muted">{c.plan || "—"}</td>
                <td className="px-4 py-3">
                  <span
                    className={
                      c.status === "active"
                        ? "rounded bg-signal-900/40 px-1.5 py-0.5 text-xs text-signal-300"
                        : "rounded bg-surface-3 px-1.5 py-0.5 text-xs text-ink-muted"
                    }
                  >
                    {c.status || "—"}
                  </span>
                </td>
                <td className="px-4 py-3 tabular-nums text-ink-muted">
                  {c.leads_count ?? 0}
                </td>
                <td className="px-4 py-3 text-right">
                  <Link
                    href={`/admin/leads/${c.id}`}
                    className="text-sm text-brand-500 hover:text-brand-700 dark:text-brand-300"
                  >
                    View leads →
                  </Link>
                </td>
              </tr>
            ))}
            {!loading && clients.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-10 text-center text-ink-faint">
                  No clients yet. Create one under Clients, then run the pipeline.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
