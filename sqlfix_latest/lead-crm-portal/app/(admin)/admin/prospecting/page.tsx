"use client";

/**
 * Our own lead gen — the same engine pointed at agencies instead of at a
 * client's local market. Results land in an internal workspace, so the
 * whole client CRM (leads, sequences, replies, deals) works on them
 * unchanged.
 */
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { adminExtras } from "@/lib/admin";

const DEFAULT_QUERIES = [
  "marketing agency",
  "digital marketing agency",
  "lead generation agency",
  "local seo agency",
];

export default function AdminProspectingPage() {
  const [data, setData] = useState<any>(null);
  const [locations, setLocations] = useState("New Jersey, New York");
  const [queries, setQueries] = useState(DEFAULT_QUERIES.join(", "));
  const [limitPer, setLimitPer] = useState(40);
  const [target, setTarget] = useState(0);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await adminExtras.prospecting());
      setError("");
    } catch (e: any) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function run(fn: () => Promise<any>, ok: string) {
    setBusy(true);
    setMsg("");
    try {
      const r = await fn();
      setMsg(r?.job_id ? `${ok} (job ${String(r.job_id).slice(0, 8)})` : ok);
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const configured = data?.configured;
  const list = (s: string) =>
    s
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);

  return (
    <div className="page">
      <header>
        <h1 className="page-title">Our prospecting</h1>
        <p className="page-sub">Find agencies who buy managed lead delivery</p>
      </header>

      {error && <p className="text-sm text-rust-500 dark:text-rust-300">{error}</p>}
      {msg && <p className="text-sm text-signal-600 dark:text-signal-300">{msg}</p>}

      {!configured ? (
        <div className="card-pad space-y-3">
          <p className="text-sm text-ink-muted">
            The internal workspace isn&apos;t set up yet. This creates one reserved tenant, our own
            deal stages (Prospect → Contacted → Demo booked → Trial → Won/Lost) and an agency ICP.
          </p>
          <button aria-label="Action"
            disabled={busy}
            className="rounded-lg bg-signal-600 px-3 py-2 text-sm font-medium text-white hover:bg-signal-700 disabled:opacity-40"
            onClick={() => run(() => adminExtras.prospectingSetup(), "Internal workspace ready")}
          >
            Set up internal workspace
          </button>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              ["Prospects", data.prospects],
              ["Open deals", data.deals_open],
              ["Won", data.deals_won],
              ["Replies", data.replies],
            ].map(([label, v]) => (
              <div key={String(label)} className="card-pad">
                <div className="text-xs uppercase tracking-wide text-ink-faint">{label}</div>
                <div className="mt-1 text-2xl font-semibold text-ink">{v ?? 0}</div>
              </div>
            ))}
          </div>

          <p className="text-xs text-ink-faint">
            Workspace <span className="font-mono">{data.tenant_name}</span> — open it in the client
            portal to work these like any other pipeline.{" "}
            <Link href="/admin/replies" className="text-brand-600 hover:underline dark:text-brand-700 dark:text-brand-300">
              Our replies →
            </Link>
          </p>

          <section className="card-pad space-y-3">
            <h2 className="text-sm font-semibold text-ink">Run a prospecting pass</h2>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block text-xs text-ink-muted">
                Locations (comma separated)
                <input
                  className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink"
                  value={locations}
                  onChange={(e) => setLocations(e.target.value)}
                />
              </label>
              <label className="block text-xs text-ink-muted">
                Queries
                <input
                  className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink"
                  value={queries}
                  onChange={(e) => setQueries(e.target.value)}
                />
              </label>
              <label className="block text-xs text-ink-muted">
                Limit per location
                <input
                  type="number"
                  min={1}
                  max={200}
                  className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink"
                  value={limitPer}
                  onChange={(e) => setLimitPer(Number(e.target.value))}
                />
              </label>
              <label className="block text-xs text-ink-muted">
                Target new leads (0 = single pass)
                <input
                  type="number"
                  min={0}
                  max={20000}
                  className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink"
                  value={target}
                  onChange={(e) => setTarget(Number(e.target.value))}
                />
              </label>
            </div>
            <button aria-label="Action"
              disabled={busy}
              className="rounded-lg bg-signal-600 px-3 py-2 text-sm font-medium text-white hover:bg-signal-700 disabled:opacity-40"
              onClick={() =>
                run(
                  () =>
                    adminExtras.prospectingRun({
                      locations: list(locations),
                      queries: list(queries),
                      limit_per_location: limitPer,
                      target_new_leads: target,
                    }),
                  "Queued"
                )
              }
            >
              Queue prospecting run
            </button>
            <p className="text-xs text-ink-faint">
              Runs at lower priority than client work, so it never delays a paid pipeline. Track it
              on{" "}
              <Link href="/admin/jobs" className="text-brand-600 hover:underline dark:text-brand-700 dark:text-brand-300">
                Jobs
              </Link>
              .
            </p>
          </section>

          {!!(data.recent_runs || []).length && (
            <section className="space-y-2">
              <h2 className="text-sm font-semibold text-ink">Recent runs</h2>
              <div className="space-y-2">
                {data.recent_runs.map((r: any) => (
                  <div key={r.id} className="card-pad flex flex-wrap items-center justify-between gap-2">
                    <div className="text-sm text-ink">
                      {r.status} · {r.discovered ?? 0} found · {r.assigned ?? 0} kept
                    </div>
                    <div className="text-xs text-ink-faint">
                      {r.started_at ? new Date(r.started_at).toLocaleString() : ""}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
